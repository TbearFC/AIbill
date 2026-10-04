import json, pathlib, tempfile, unittest
from agentcost.core import Call, Ledger, Prices, normalize, timestamp
from agentcost.adapters import parse_codex, parse_claude, parse_generic, codex_files
from agentcost.analysis import summarize, remaining_forecast
from agentcost.report import write_report


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, events, name="events.jsonl"):
        p = self.folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(e) + "\n" for e in events))
        return p

    def codevent(self, typ, v, ts="2026-10-01T00:00:00Z"):
        return {"timestamp": ts, "type": typ, "payload": v}

    def usage(self, i, o, cache=0):
        return {"input_tokens": i, "output_tokens": o, "cached_input_tokens": cache}

    def test_openai_cache_and_reasoning_not_double_counted(self):
        b, _ = normalize(
            {
                "input_tokens": 10000,
                "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 8000, "cache_write_tokens": 1000},
                "output_tokens_details": {"reasoning_tokens": 400},
            },
            "openai",
        )
        self.assertEqual(sum(b.values()), 10500)
        self.assertEqual(b["input_uncached"], 1000)

    def test_anthropic_exclusive_ttl(self):
        b, f = normalize(
            {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 8000,
                "cache_creation_input_tokens": 1000,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 400,
                    "ephemeral_1h_input_tokens": 600,
                },
            },
            "anthropic",
        )
        self.assertEqual(sum(b.values()), 10500)
        self.assertFalse(f)

    def test_bad_usage_rejected(self):
        for u in [
            self.usage(10, 0, 20),
            self.usage(-1, 0),
            self.usage(True, 0),
            self.usage(1, float("nan")),
        ]:
            with self.assertRaises(ValueError):
                normalize(u, "openai")

    def test_missing_usage_is_not_zero(self):
        with self.assertRaises(ValueError):
            normalize({"input_tokens": 4}, "openai")

    def test_cache_ttl_unknown_price_not_zero(self):
        b, f = normalize(
            {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 1}, "anthropic"
        )
        c = Call(
            "claude", "s", "r", "q", "2026-10-01", "example-anthropic-model", b, "test", flags=f
        )
        self.assertEqual(Prices().quote(c), (None, "unknown_cache_ttl"))

    def test_example_price_long_context_and_fast(self):
        b, _ = normalize(self.usage(300000, 100000), "openai")
        c = Call(
            "codex", "s", "r", "q", "2026-10-01", "example-openai-model", b, "test", tier="fast"
        )
        self.assertAlmostEqual(Prices().quote(c)[0], 5.4)

    def test_unknown_model_keeps_tokens_and_null_cost(self):
        l = Ledger()
        b, _ = normalize(self.usage(100, 10), "openai")
        l.add(Call("a", "s", "r", "q", timestamp("2026-10-01T00:00:00Z"), "unknown", b, "test"))
        l.finalize()
        d = summarize(l, Prices(), timestamp("2026-10-03T00:00:00Z"))
        self.assertEqual(d["totals"]["tokens"], 110)
        self.assertIsNone(d["totals"]["total_api_equivalent_usd"])
        self.assertEqual(d["totals"]["priced_records"], 0)

    def test_cumulative_repeats_and_archive_precedence(self):
        ev = [
            self.codevent("session_meta", {"id": "s", "source": "cli"}),
            self.codevent("turn_context", {"turn_id": "r", "model": "example-openai-model"}),
        ]
        for i, u in enumerate([self.usage(100, 10), self.usage(100, 10), self.usage(200, 20)]):
            ev.append(
                self.codevent(
                    "event_msg",
                    {"type": "token_count", "info": {"total_token_usage": u}},
                    f"2026-10-01T00:0{i}:00Z",
                )
            )
        p = self.file(ev, "sessions/a.jsonl")
        self.file(ev, "archived_sessions/a.jsonl")
        self.assertEqual(codex_files(self.folder), [p])
        l = Ledger()
        parse_codex(p, l, timestamp("2026-10-03T00:00:00Z"))
        self.assertEqual(sum(c.tokens for c in l.calls.values()), 220)

    def test_explicit_provider_records_suppress_counters(self):
        ev = [
            self.codevent("session_meta", {"id": "s"}),
            self.codevent(
                "event_msg",
                {"type": "token_count", "info": {"total_token_usage": self.usage(100, 10)}},
            ),
            self.codevent(
                "token_usage_record",
                {
                    "response_id": "q",
                    "root_turn_id": "r",
                    "thread_id": "s",
                    "usage": self.usage(100, 10),
                },
            ),
        ]
        l = Ledger()
        p = self.file(ev)
        parse_codex(p, l, timestamp("2026-10-03T00:00:00Z"))
        parse_codex(p, l, timestamp("2026-10-03T00:00:00Z"))
        l.finalize()
        self.assertEqual(sum(c.tokens for c in l.calls.values()), 110)

    def test_child_cumulative_replay_is_withheld(self):
        p = self.file(
            [
                self.codevent(
                    "session_meta",
                    {"id": "child", "parent_thread_id": "p", "subagent_history_start_ordinal": 5},
                ),
                self.codevent(
                    "event_msg",
                    {"type": "token_count", "info": {"total_token_usage": self.usage(10000, 1000)}},
                ),
            ]
        )
        l = Ledger()
        parse_codex(p, l, timestamp("2026-10-03T00:00:00Z"))
        self.assertFalse(l.calls)
        self.assertEqual(l.audit["withheld_child_cumulative_snapshots"], 1)

    def test_claude_stream_merge_and_time_visibility(self):
        def event(t, out):
            return {
                "timestamp": t,
                "type": "assistant",
                "sessionId": "s",
                "message": {
                    "id": "m",
                    "model": "example-anthropic-model",
                    "usage": {"input_tokens": 100, "output_tokens": out},
                },
            }

        p = self.file([event("2026-10-01T00:00:00Z", 1), event("2026-10-01T00:01:00Z", 20)])
        l = Ledger()
        parse_claude(p, l, timestamp("2026-10-01T00:00:30Z"))
        self.assertEqual(sum(c.tokens for c in l.calls.values()), 101)
        l2 = Ledger()
        parse_claude(p, l2, timestamp("2026-10-03T00:00:00Z"))
        self.assertEqual(len(l2.calls), 1)
        self.assertEqual(sum(c.tokens for c in l2.calls.values()), 120)
        self.assertEqual(next(iter(l2.calls.values())).ts, timestamp("2026-10-01T00:01:00Z"))

    def test_generic_contract_validation(self):
        p = self.file(
            [
                dict(
                    type="usage",
                    timestamp="2026-10-01T00:00:00Z",
                    agent="other",
                    run_id="r",
                    session_id="s",
                    response_id="q",
                    usage={"input_uncached": 100, "output": 20},
                ),
                dict(
                    type="usage",
                    timestamp="2026-10-01T00:00:00Z",
                    agent="other",
                    usage={"output": 4},
                ),
            ]
        )
        l = Ledger()
        parse_generic(p, l, timestamp("2026-10-03T00:00:00Z"))
        self.assertEqual(sum(c.tokens for c in l.calls.values()), 120)
        self.assertEqual(l.audit["invalid_generic_contract"], 1)

    def forecast_ledger(self):
        l = Ledger()
        for i in range(25):
            ts = f"2026-09-{i+1:02}T00:00:00Z"
            for j in range(2):
                b, _ = normalize(self.usage(100, 10), "openai")
                l.add(
                    Call(
                        "codex",
                        str(i),
                        str(i),
                        f"{i}-{j}",
                        timestamp(ts),
                        "example-openai-model",
                        b,
                        "test",
                    )
                )
            l.ends["codex", str(i)] = timestamp(f"2026-09-{i+1:02}T01:00:00Z")
        b, _ = normalize(self.usage(200, 20), "openai")
        l.add(
            Call(
                "codex",
                "query",
                "query",
                "query",
                timestamp("2026-10-01T00:00:00Z"),
                "example-openai-model",
                b,
                "test",
            )
        )
        l.finalize()
        return l

    def test_forecast_excludes_future_labels_and_same_family(self):
        l = self.forecast_ledger()
        rows = l.rows(timestamp("2026-10-03T00:00:00Z"))
        a = remaining_forecast(l, rows, "codex", "latest")
        self.assertEqual(a["remaining_p50_tokens"], 110)
        # Change a historical future label whose completion is after query: it
        # must not be eligible even though its usage appears in a loaded file.
        l.ends["codex", "0"] = timestamp("2026-10-02T00:00:00Z")
        for c in rows:
            if c.run == "0":
                c.buckets["output"] = 10**9
        b = remaining_forecast(l, rows, "codex", "latest")
        self.assertEqual(b["remaining_p50_tokens"], 110)
        self.assertEqual(b["history_runs"], 24)
        l.join(("run", "codex", "1"), ("run", "codex", "query"))
        c = remaining_forecast(l, rows, "codex", "latest")
        self.assertEqual(c["history_runs"], 23)

    def test_support_threshold_does_not_invent_forecast(self):
        l = self.forecast_ledger()
        rows = l.rows(timestamp("2026-10-03T00:00:00Z"))
        for i in range(25):
            l.join(("run", "codex", str(i)), ("family", "all"))
        f = remaining_forecast(l, rows, "codex", "latest")
        self.assertEqual(f["status"], "insufficient_history")
        self.assertNotIn("remaining_p50_tokens", f)

    def test_stale_open_interaction_not_reported_current(self):
        l = self.forecast_ledger()
        f = remaining_forecast(
            l,
            l.rows(timestamp("2026-10-03T00:00:00Z")),
            "codex",
            "latest",
            as_of=timestamp("2026-10-03T00:00:00Z"),
        )
        self.assertEqual(f["status"], "no_open_or_eligible_interaction")

    def test_dollar_forecast_requires_complete_price_support(self):
        l = self.forecast_ledger()
        rows = l.rows(timestamp("2026-10-03T00:00:00Z"))
        f = remaining_forecast(l, rows, "codex", "latest", prices=Prices())
        self.assertIsNotNone(f["dollar_forecast"])
        self.assertAlmostEqual(f["dollar_forecast"]["p50_usd"], 0.0003)
        l.calls["codex", "0-1"].model = "missing"
        f = remaining_forecast(l, rows, "codex", "latest", prices=Prices())
        self.assertIsNone(f["dollar_forecast"])

    def test_render_does_not_leak_ids_or_allow_script_escape(self):
        l = self.forecast_ledger()
        d = summarize(l, Prices(), timestamp("2026-10-03T00:00:00Z"))
        d["models"][0]["model"] = "</script><script>alert(1)</script>"
        p = write_report(d, self.folder / "out")
        html = p.read_text()
        self.assertNotIn("</script><script>alert", html)
        self.assertNotIn('"request":', html)
        self.assertNotIn('"session":', html)
        self.assertIn("default-src", html)

    def test_complete_run_rate_window_and_partial_prices(self):
        l = self.forecast_ledger()
        d = summarize(l, Prices(), timestamp("2026-10-03T00:00:00Z"))
        rr = d["run_rate"]["codex"]
        self.assertEqual(rr["status"], "conditional_extrapolation")
        next(iter(l.calls.values())).model = "missing"
        # A missing price in the actual rate window disables the dollar rate.
        for c in l.calls.values():
            if c.ts[:10] == "2026-10-01":
                c.model = "missing"
        d = summarize(l, Prices(), timestamp("2026-10-03T00:00:00Z"))
        self.assertIsNone(d["run_rate"]["codex"]["next_30_days_reference_usd_if_usage_unchanged"])


if __name__ == "__main__":
    unittest.main()
