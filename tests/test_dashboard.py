import http.client
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

from agentcost.api import AnalysisOptions, LogSource, analyze_sources
from agentcost.dashboard import make_server
from agentcost.demo import make_demo
from agentcost.report import write_report


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.tmp.name)
        make_demo(self.folder / "inputs")
        sources = [LogSource("generic", p) for p in (self.folder / "inputs").glob("*.jsonl")]
        self.data = analyze_sources(sources, AnalysisOptions("2026-10-03T13:35:00Z"))
        self.data["synthetic_demo"] = True
        self.report = write_report(self.data, self.folder / "out")
        (self.folder / "out" / "private.jsonl").write_text("synthetic-private-canary")
        self.server = make_server(self.report, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_workspace_and_aggregates_are_served(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        html = body.decode()
        self.assertIn("研究工作台", html)
        for view in ("overview", "models", "forecast", "quality"):
            self.assertIn('data-page="' + view + '"', html)
        self.assertNotIn("__PAYLOAD__", html)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("synthetic-private-canary", html)
        status, _, body = self.request("/usage.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["totals"]["tokens"], self.data["totals"]["tokens"])

    def test_paths_cannot_reach_logs_or_directory_listings(self):
        for path in (
            "/private.jsonl",
            "/synthetic_inputs/",
            "/../private.jsonl",
            "/%2e%2e/README.md",
            "/README.md",
        ):
            self.assertEqual(self.request(path)[0], 404)

    def test_host_check_and_read_only_methods(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("/", headers={"Host": "foreign.example"})[0], 403)
        self.assertEqual(self.request("/", method="POST")[0], 501)
        status, _, body = self.request("/healthz", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_server_holds_the_generated_snapshot(self):
        self.report.write_text("changed-after-start")
        self.assertNotEqual(self.request("/")[2], b"changed-after-start")

    def test_launcher_default_is_synthetic_and_does_not_scan_agent_roots(self):
        # Execute the exact double-click launcher, with explicitly absent private roots.
        process = subprocess.Popen(
            [
                str(ROOT / "start.command"),
                "--port",
                "0",
                "--out",
                str(self.folder / "launch"),
                "--codex-home",
                str(self.folder / "absent"),
                "--claude-home",
                str(self.folder / "absent"),
            ],
            cwd=self.folder,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Ready marker is flushed only after binding; cap the wait without hanging tests.
            import select

            ready = False
            for _ in range(100):
                # TextIO buffering can hold lines after select; use readiness via health probe
                # by collecting process stdout bytes directly until the marker arrives.
                readable, _, _ = select.select([process.stdout], [], [], 0.1)
                if readable:
                    import os

                    chunk = os.read(process.stdout.fileno(), 65536).decode()
                    if "Dashboard: " in chunk:
                        url = chunk.split("Dashboard: ", 1)[1].splitlines()[0]
                        ready = True
                        break
                if process.poll() is not None:
                    break
            self.assertTrue(ready, "launcher did not become ready")
            port = int(url.split(":")[2].rstrip("/"))
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/healthz")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
            data = json.loads((self.folder / "launch/usage.json").read_text())
            self.assertTrue(data["synthetic_demo"])
            self.assertEqual(data["source_inventory"]["codex_files"], 0)
            self.assertEqual(data["source_inventory"]["claude_files"], 0)
            self.assertGreater(data["totals"]["tokens"], 0)
            self.assertTrue(any(f["status"] == "experimental_prior" for f in data["forecasts"]))
        finally:
            process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
