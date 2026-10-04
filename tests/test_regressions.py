"""Regression and CLI integration checks beyond the handoff fixtures."""

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from agentcost.__main__ import main
from agentcost.adapters import parse_codex, parse_generic
from agentcost.analysis import remaining_forecast
from agentcost.core import Call, Ledger, Prices, normalize, timestamp

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def events(self, rows):
        path = self.folder / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def call(self, ledger, run, ts, tokens=110, session=None, request=None):
        buckets, _ = normalize({"input_uncached": tokens - 10, "output": 10}, "exclusive")
        ledger.add(
            Call(
                "codex",
                session or run,
                run,
                request or run + ts,
                timestamp(ts),
                "example-openai-model",
                buckets,
                "test",
            )
        )

    def query(self):
        ledger = Ledger()
        for i in range(25):
            run = "history-" + str(i)
            self.call(ledger, run, f"2026-09-{i+1:02}T00:00:00Z")
            self.call(ledger, run, f"2026-09-{i+1:02}T00:01:00Z")
            ledger.ends["codex", run] = timestamp(f"2026-09-{i+1:02}T01:00:00Z")
        self.call(ledger, "query", "2026-10-01T00:00:00Z", 220)
        ledger.finalize()
        return ledger

    def forecast(self, ledger, **kw):
        now = timestamp("2026-10-01T01:00:00Z")
        return remaining_forecast(ledger, ledger.rows(now), "codex", as_of=now, **kw)

    def test_completion_after_last_usage_closes_latest(self):
        ledger = self.query()
        ledger.ends["codex", "query"] = timestamp("2026-10-01T00:10:00Z")
        self.assertEqual(self.forecast(ledger)["status"], "no_open_or_eligible_interaction")

    def test_future_completion_does_not_close_current_prefix(self):
        ledger = self.query()
        ledger.ends["codex", "query"] = timestamp("2026-10-02T00:10:00Z")
        self.assertEqual(self.forecast(ledger)["status"], "experimental_prior")

    def test_fixed_checkpoint_selection_ignores_future_calls(self):
        ledger = self.query()
        self.call(ledger, "older-query", "2026-09-30T00:00:00Z")
        self.call(ledger, "older-query", "2026-10-01T00:30:00Z", 9999)
        result = self.forecast(ledger, k="1")
        self.assertEqual(result["snapshot_time"], timestamp("2026-10-01T00:00:00Z"))
        self.assertEqual(result["spent_tokens"], 220)

    def test_fixed_checkpoint_can_replay_completed_query(self):
        ledger = self.query()
        ledger.ends["codex", "query"] = timestamp("2026-10-01T00:10:00Z")
        self.assertEqual(self.forecast(ledger, k="1")["status"], "experimental_prior")

    def test_equal_budget_is_not_already_exceeded(self):
        result = self.forecast(self.query(), budget=220)
        self.assertFalse(result["already_over_budget"])
        self.assertEqual(result["empirical_exceedance_fraction"], 1)

    def test_zero_remaining_history_retained(self):
        ledger = self.query()
        for key in list(ledger.calls):
            if ledger.calls[key].ts.endswith("00:01:00.000000Z"):
                del ledger.calls[key]
        result = self.forecast(ledger, budget=220)
        self.assertEqual(result["remaining_p90_tokens"], 0)
        self.assertEqual(result["empirical_exceedance_fraction"], 0)

    def test_late_subagent_flag_excludes_query(self):
        ledger = self.query()
        self.call(ledger, "query", "2026-10-01T00:01:00Z")
        list(ledger.calls.values())[-1].flags.append("subagent_in_session_family")
        self.assertEqual(self.forecast(ledger)["status"], "no_open_or_eligible_interaction")

    def test_fixed_checkpoint_ignores_future_attribution_flags(self):
        ledger = self.query()
        self.call(ledger, "query", "2026-10-01T00:01:00Z")
        list(ledger.calls.values())[-1].flags.append("subagent_in_session_family")
        self.assertEqual(self.forecast(ledger, k="1")["status"], "experimental_prior")

    def test_malformed_nested_usage_rejected(self):
        for schema, usage in [
            ("openai", {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": []}),
            ("openai", {"input_tokens": 1, "output_tokens": 1, "prompt_tokens_details": "bad"}),
            ("anthropic", {"input_tokens": 1, "output_tokens": 1, "cache_creation": []}),
        ]:
            with self.subTest(schema=schema, usage=usage), self.assertRaises(ValueError):
                normalize(usage, schema)

    def test_malformed_codex_info_is_audited(self):
        rows = [
            {
                "type": "event_msg",
                "timestamp": "2026-10-01T00:00:00Z",
                "payload": {"type": "token_count", "info": ["bad"]},
            }
        ]
        ledger = Ledger()
        parse_codex(self.events(rows), ledger, timestamp("2026-10-02T00:00:00Z"))
        self.assertEqual(ledger.audit["invalid_usage"], 1)

    def test_invalid_generic_identifiers_not_coerced(self):
        valid = dict(
            type="usage",
            timestamp="2026-10-01T00:00:00Z",
            agent="other",
            run_id="run",
            session_id="s",
            response_id="q",
            usage={"input_uncached": 100, "output": 20},
        )
        rows = []
        for name in ("agent", "run_id", "session_id", "response_id"):
            for value in ("", " ", None, [], False, 42):
                rows.append(dict(valid, **{name: value}))
        ledger = Ledger()
        parse_generic(self.events(rows), ledger, timestamp("2026-10-02T00:00:00Z"))
        self.assertFalse(ledger.calls)
        self.assertEqual(ledger.audit["invalid_generic_contract"], 24)

    def test_out_of_order_generic_completions_keep_latest(self):
        rows = [
            dict(type="task_complete", timestamp=t, agent="other", run_id="r")
            for t in ("2026-10-01T00:02:00Z", "2026-10-01T00:01:00Z")
        ]
        ledger = Ledger()
        parse_generic(self.events(rows), ledger, timestamp("2026-10-02T00:00:00Z"))
        self.assertEqual(ledger.ends["other", "r"], timestamp("2026-10-01T00:02:00Z"))

    def test_invalid_price_multiplier_rejected(self):
        for field in ("tier_multipliers", "geo_multipliers", "long_context_multipliers"):
            for value in (-1, True, float("nan"), float("inf"), "2"):
                data = json.loads((ROOT / "agentcost/prices.json").read_text())
                data["models"]["example-openai-model"][field] = {"fast": value}
                path = self.folder / "prices.json"
                path.write_text(json.dumps(data))
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Prices(path)

    def test_bad_price_catalog_structure_rejected(self):
        for data in (
            [],
            {"version": "x", "basis": "x", "models": []},
            {"version": "x", "basis": "x", "models": {}, "aliases": {"bad": "absent"}},
        ):
            path = self.folder / "prices.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                Prices(path)

    def test_date_offset_normalizes_to_utc(self):
        self.assertEqual(timestamp("2026-10-01T08:00:00+08:00"), timestamp("2026-10-01T00:00:00Z"))

    def test_cli_missing_explicit_paths_fail_before_writing(self):
        for option in ("--input", "--codex-home", "--claude-home"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(
                    [
                        "report",
                        option,
                        str(self.folder / "missing"),
                        "--out",
                        str(self.folder / "out"),
                    ]
                )
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((self.folder / "out").exists())

    def test_cli_invalid_options_are_usage_errors(self):
        for options in (
            ["--k", "0"],
            ["--k", "-1"],
            ["--budget-tokens", "0"],
            ["--since", "2026-10-03", "--until", "2026-10-01"],
            ["--as-of", "2026-10-01T00:00:00"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["demo"] + options + ["--out", str(self.folder / "out")])
            self.assertEqual(error.exception.code, 2)

    def test_cli_empty_inputs_report_gap_and_fail(self):
        path = self.events([])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(
                [
                    "report",
                    "--agent",
                    "generic",
                    "--input",
                    str(path),
                    "--out",
                    str(self.folder / "out"),
                ]
            )
        self.assertEqual(code, 1)
        data = json.loads((self.folder / "out/usage.json").read_text())
        self.assertEqual(data["scan_status"], "no_accepted_usage")
        self.assertIsNone(data["totals"]["total_api_equivalent_usd"])

    def test_module_demo_and_launcher_are_equivalent(self):
        outputs = []
        for command, name in [
            ([sys.executable, "-m", "agentcost"], "module"),
            ([sys.executable, str(ROOT / "agentcost_cli.py")], "launcher"),
        ]:
            out = self.folder / name
            result = subprocess.run(
                command + ["demo", "--as-of", "2026-10-03T13:35:00Z", "--out", str(out)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads((out / "usage.json").read_text()))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0]["totals"]["records"], 610)
        self.assertEqual(outputs[0]["totals"]["tokens"], 5511100)
        self.assertTrue(outputs[0]["synthetic_demo"])

    def test_until_caps_ingestion_and_since_only_filters_report(self):
        path = self.events(
            [
                dict(
                    type="usage",
                    timestamp=t,
                    agent="a",
                    session_id="s",
                    run_id=t,
                    response_id=t,
                    usage={"input_uncached": 100, "output": 20},
                )
                for t in ("2026-10-01T23:59:59Z", "2026-10-02T00:00:00Z")
            ]
        )
        with contextlib.redirect_stdout(io.StringIO()):
            main(
                [
                    "report",
                    "--agent",
                    "generic",
                    "--input",
                    str(path),
                    "--until",
                    "2026-10-01",
                    "--as-of",
                    "2026-10-03T00:00:00Z",
                    "--out",
                    str(self.folder / "out"),
                ]
            )
        data = json.loads((self.folder / "out/usage.json").read_text())
        self.assertEqual(data["totals"]["tokens"], 120)


if __name__ == "__main__":
    unittest.main()
