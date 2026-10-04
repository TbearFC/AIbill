import http.client
import json
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from agentcost.api import AnalysisOptions, discover_sources
from agentcost.dashboard import make_server
from agentcost.local import LocalCollector, LocalConfig, load_config


ROOT = pathlib.Path(__file__).resolve().parents[1]


def codex_events(response="fixture-response", input_tokens=100, output_tokens=20):
    return [
        {
            "type": "session_meta",
            "timestamp": "2026-10-01T00:00:00Z",
            "payload": {"id": "fixture-session", "source": "cli"},
        },
        {
            "type": "token_usage_record",
            "timestamp": "2026-10-01T00:01:00Z",
            "payload": {
                "response_id": response,
                "thread_id": "fixture-session",
                "root_turn_id": "fixture-turn",
                "model": "fixture-unpriced-model",
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                "prompt": "private-content-canary",
            },
        },
    ]


def claude_events():
    return [
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:00Z",
            "sessionId": "fixture-claude-session",
            "uuid": "fixture-human",
            "message": {"content": "private-content-canary"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-01T00:01:00Z",
            "sessionId": "fixture-claude-session",
            "message": {
                "id": "fixture-claude-response",
                "model": "fixture-unpriced-claude",
                "content": "private-content-canary",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 30,
                    "cache_read_input_tokens": 70,
                    "cache_creation_input_tokens": 10,
                    "output_tokens": 15,
                },
            },
        },
    ]


def write_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class LocalLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "private-root-canary"
        self.codex = self.root / "codex"
        self.claude = self.root / "claude"
        self.codex_log = self.codex / "sessions/current.jsonl"
        write_events(self.codex_log, codex_events())
        write_events(self.claude / "projects/example/current.jsonl", claude_events())
        self.config = LocalConfig(str(self.codex), str(self.claude))
        self.collector = LocalCollector(
            self.config, self.root / "out", options=AnalysisOptions("2026-10-03T00:00:00Z")
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_schema_totals_sources_and_private_configuration(self):
        data = self.collector.collect()
        self.assertEqual(data["totals"]["tokens"], 245)
        self.assertEqual(data["totals"]["records"], 2)
        self.assertEqual([s["tokens"] for s in data["collection"]["sources"]], [120, 125])
        self.assertTrue(all(s["status"] == "ready" for s in data["collection"]["sources"]))
        self.assertIsNone(data["totals"]["total_api_equivalent_usd"])
        self.assertNotIn("synthetic_demo", data)
        report = self.collector.publish(data)
        exported = report.read_text() + report.with_name("usage.json").read_text()
        for secret in (
            "private-root-canary",
            "private-content-canary",
            "fixture-response",
            "fixture-session",
            "fixture-claude-response",
        ):
            self.assertNotIn(secret, exported)
        settings = report.with_name("sources.json")
        self.assertEqual(load_config(settings, self.config), self.config.updated({}))
        self.assertEqual(settings.stat().st_mode & 0o777, 0o600)

    def test_missing_and_empty_roots_are_not_filled_with_demo(self):
        empty = self.root / "empty"
        empty.mkdir()
        cfg = LocalConfig(str(self.root / "absent"), str(empty))
        data = self.collector.collect(cfg)
        self.assertEqual(data["totals"]["records"], 0)
        self.assertEqual(
            [s["status"] for s in data["collection"]["sources"]],
            ["missing_directory", "empty_directory"],
        )
        self.assertNotIn("synthetic_demo", data)

    def test_nested_claude_subagent_and_stream_records_merge(self):
        rows = claude_events()
        rows[-1]["message"]["usage"]["output_tokens"] = 20
        write_events(self.claude / "projects/example/subagents/agent-fixture.jsonl", rows)
        data = self.collector.collect()
        self.assertEqual(data["totals"]["tokens"], 250)
        self.assertEqual(data["source_inventory"]["claude_files"], 2)
        self.assertEqual(data["log_observations"]["claude"]["records"], 1)
        self.assertGreater(data["audit"]["duplicate_or_stream_updates"], 0)

    def test_generated_snapshots_and_settings_are_ignored_in_any_output_directory(self):
        repository = self.root / "ignore-fixture"
        repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        (repository / ".gitignore").write_text((ROOT / ".gitignore").read_text())
        for filename in (
            "usage.json",
            "report.html",
            "sources.json",
            ".sources.tmp",
            "nested/usage.json",
        ):
            result = subprocess.run(["git", "check-ignore", "--quiet", filename], cwd=repository)
            self.assertEqual(result.returncode, 0, filename)

    def test_overlapping_codex_roots_do_not_inflate_file_counts(self):
        sources = discover_sources(
            agent="codex", codex_homes=[self.codex, self.codex / "sessions", self.codex]
        )
        self.assertEqual(len(sources), 1)

    def test_relative_settings_resolve_for_reuse_and_file_roots_are_rejected(self):
        config = LocalConfig(".", ".").updated({})
        self.assertTrue(pathlib.Path(config.codex_home).is_absolute())
        with self.assertRaises(ValueError):
            self.config.updated({"codex_home": str(self.codex_log)})
        with self.assertRaises(ValueError):
            self.config.updated({"codex_home": ", ,"})

    def test_model_daily_sums_match_aggregate_truth(self):
        write_events(self.codex_log, codex_events() + codex_events("second-response", 50, 5)[1:])
        data = self.collector.collect()
        self.assertEqual(sum(row["tokens"] for row in data["model_daily"]), 300)
        self.assertEqual(sum(row["records"] for row in data["model_daily"]), 3)

    def test_live_cli_rejects_explicit_missing_input_before_output(self):
        import contextlib
        import io
        from agentcost.__main__ import main

        out = self.root / "invalid-output"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(
                [
                    "dashboard",
                    "--agent",
                    "generic",
                    "--input",
                    str(self.root / "missing.jsonl"),
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(out.exists())

    def test_default_launcher_reads_both_configured_sources(self):
        from agentcost.__main__ import main

        with mock.patch("agentcost.dashboard.serve_report") as serve, mock.patch("builtins.print"):
            code = main(
                [
                    "dashboard",
                    "--codex-home",
                    str(self.codex),
                    "--claude-home",
                    str(self.claude),
                    "--out",
                    str(self.root / "launch"),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIsInstance(serve.call_args.args[2], LocalCollector)
        data = json.loads((self.root / "launch/usage.json").read_text())
        self.assertEqual(data["totals"]["tokens"], 245)
        self.assertNotIn("synthetic_demo", data)

    def test_environment_config_roots_override_saved_settings(self):
        from agentcost.__main__ import main

        out = self.root / "saved"
        out.mkdir()
        (out / "sources.json").write_text(
            json.dumps(
                {
                    "codex_home": str(self.root / "missing"),
                    "claude_home": str(self.root / "missing"),
                }
            )
        )
        with (
            mock.patch.dict(
                "os.environ", {"CODEX_HOME": str(self.codex), "CLAUDE_CONFIG_DIR": str(self.claude)}
            ),
            mock.patch("agentcost.dashboard.serve_report"),
            mock.patch("builtins.print"),
        ):
            main(["dashboard", "--out", str(out)])
        self.assertEqual(json.loads((out / "usage.json").read_text())["totals"]["tokens"], 245)

    def test_root_metadata_object_is_not_automatically_a_child(self):
        rows = [
            {
                "type": "session_meta",
                "timestamp": "2026-10-01T00:00:00Z",
                "payload": {"id": "root", "source": {"cli": {}}},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-10-01T00:01:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 20}},
                },
            },
        ]
        write_events(self.codex_log, rows)
        self.assertEqual(self.collector.collect()["log_observations"]["codex"]["tokens"], 120)


class RefreshAPI(unittest.TestCase):
    def setUp(self):
        LocalLogs.setUp(self)
        self.report = self.collector.publish(self.collector.collect())
        self.server = make_server(self.report, 0, self.collector)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.server.snapshot.worker:
            self.server.snapshot.worker.join(timeout=5)
        LocalLogs.tearDown(self)

    def request(self, path, method="GET", payload=None, headers=None):
        port = self.server.server_port
        defaults = {
            "Origin": "http://127.0.0.1:" + str(port),
            "X-AgentCost-Local": "1",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            method,
            path,
            body=json.dumps(payload if payload is not None else {}) if method == "POST" else None,
            headers=defaults if headers is None else headers,
        )
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def wait_scan(self):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            status = json.loads(self.request("/api/status")[1])
            if not status["busy"]:
                return status
            time.sleep(0.01)
        self.fail("scan did not finish")

    def test_refresh_reloads_appended_usage_atomically(self):
        write_events(self.codex_log, codex_events() + codex_events("second-response", 50, 5)[1:])
        self.assertEqual(self.request("/api/refresh", "POST")[0], 202)
        self.assertEqual(self.wait_scan()["generation"], 2)
        data = json.loads(self.request("/usage.json")[1])
        self.assertEqual(data["totals"]["tokens"], 300)
        self.assertIn(b'"tokens": 300', self.request("/")[1])

    def test_custom_roots_are_local_only_and_can_replace_missing_sources(self):
        changed = self.root / "other-claude"
        write_events(changed / "projects/fixture/other.jsonl", claude_events())
        self.assertEqual(
            self.request(
                "/api/refresh",
                "POST",
                {"codex_home": str(self.root / "absent"), "claude_home": str(changed)},
            )[0],
            202,
        )
        self.assertIsNone(self.wait_scan()["error"])
        data = json.loads(self.request("/usage.json")[1])
        self.assertEqual(data["totals"]["tokens"], 125)
        self.assertEqual(data["collection"]["sources"][0]["status"], "missing_directory")
        self.assertNotIn("private-root-canary", self.request("/")[1].decode())
        self.assertIn(str(changed), self.request("/api/settings")[1].decode())
        self.assertEqual(self.request("/sources.json")[0], 404)

    def test_cross_origin_and_form_requests_cannot_scan(self):
        for headers in (
            {},
            {
                "Origin": "https://foreign.example",
                "X-AgentCost-Local": "1",
                "Content-Type": "application/json",
            },
            {
                "Origin": "http://127.0.0.1:" + str(self.server.server_port),
                "Content-Type": "application/json",
            },
        ):
            self.assertEqual(self.request("/api/refresh", "POST", headers=headers)[0], 403)
        self.assertEqual(self.request("/api/refresh")[0], 404)
        self.assertEqual(json.loads(self.request("/api/status")[1])["generation"], 1)

    def test_invalid_settings_do_not_disclose_values_or_replace_snapshot(self):
        for payload in (
            {"extra": "private-path-canary"},
            {"codex_home": 42},
            {"price_path": str(self.root / "secret-price-canary.json")},
            {"codex_home": ""},
        ):
            status, body = self.request("/api/refresh", "POST", payload)
            self.assertEqual(status, 400)
            self.assertNotIn(b"canary", body)
        self.assertEqual(json.loads(self.request("/usage.json")[1])["totals"]["tokens"], 245)

    def test_scan_failure_retains_complete_snapshot(self):
        with mock.patch.object(
            self.collector, "collect", side_effect=RuntimeError("private-exception-canary")
        ):
            self.assertEqual(self.request("/api/refresh", "POST")[0], 202)
            status = self.wait_scan()
        self.assertEqual(status["error"], "scan_failed_previous_snapshot_retained")
        self.assertEqual(status["generation"], 1)
        self.assertNotIn("canary", json.dumps(status))
        self.assertEqual(json.loads(self.request("/usage.json")[1])["totals"]["tokens"], 245)

    def test_parallel_refresh_preserves_last_snapshot_until_complete(self):
        entered, release = threading.Event(), threading.Event()
        original = self.collector.collect

        def paused(config):
            entered.set()
            release.wait(5)
            return original(config)

        with mock.patch.object(self.collector, "collect", side_effect=paused):
            try:
                self.assertEqual(self.request("/api/refresh", "POST")[0], 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request("/api/refresh", "POST")[0], 409)
                self.assertEqual(
                    json.loads(self.request("/usage.json")[1])["totals"]["tokens"], 245
                )
            finally:
                release.set()
            self.assertIsNone(self.wait_scan()["error"])


if __name__ == "__main__":
    unittest.main()
