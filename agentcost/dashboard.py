"""Serve aggregates and explicitly refresh retained logs on loopback only."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def resources_for(report):
    html = report.read_bytes()
    return {
        "/": ("text/html; charset=utf-8", html),
        "/report.html": ("text/html; charset=utf-8", html),
        "/usage.json": (
            "application/json; charset=utf-8",
            report.with_name("usage.json").read_bytes(),
        ),
        "/healthz": ("application/json", b'{"status":"ok"}\n'),
    }


class Snapshot:
    def __init__(self, report, collector):
        self.report = report
        self.collector = collector
        self.lock = threading.Lock()
        self.resources = resources_for(report)
        self.busy = False
        self.error = None
        self.generation = 1
        self.worker = None

    def status(self):
        with self.lock:
            data = json.loads(self.resources["/usage.json"][1])
            return dict(
                busy=self.busy,
                error=self.error,
                generation=self.generation,
                as_of=data["as_of"],
                refresh_supported=self.collector is not None,
            )

    def refresh(self, changes):
        with self.lock:
            if self.busy:
                return False
            config = self.collector.config.updated(changes)
            self.busy = True
            self.error = None
            self.worker = threading.Thread(target=self.collect, args=(config,), daemon=True)
            self.worker.start()
        return True

    def collect(self, config):
        try:
            data = self.collector.collect(config)
            report = self.collector.publish(data, config)
            resources = resources_for(report)
            with self.lock:
                self.resources = resources
                self.generation += 1
        except Exception:
            # Keep the last complete snapshot; never return exception text or private paths.
            with self.lock:
                self.error = "scan_failed_previous_snapshot_retained"
        finally:
            with self.lock:
                self.busy = False


def make_server(report, port=8765, collector=None):
    snapshot = Snapshot(Path(report).resolve(), collector)

    class Handler(BaseHTTPRequestHandler):
        def permitted_host(self):
            return self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port)

        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def json_response(self, status, data, head=False):
            self.send_content(
                status,
                "application/json; charset=utf-8",
                json.dumps(data, ensure_ascii=False).encode("utf8"),
                head,
            )

        def send_content(self, status, mime, content, head=False):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if not head:
                self.wfile.write(content)

        def respond(self, head):
            if not self.permitted_host():
                self.json_response(403, {"error": "loopback_host_required"}, head)
                return
            path = urlsplit(self.path).path
            if path == "/api/status":
                self.json_response(200, snapshot.status(), head)
                return
            if path == "/api/settings":
                if collector is None:
                    self.json_response(200, {"refresh_supported": False}, head)
                else:
                    self.json_response(
                        200,
                        dict(refresh_supported=True, **collector.config.public_settings()),
                        head,
                    )
                return
            with snapshot.lock:
                resource = snapshot.resources.get(path)
            if resource is None:
                self.json_response(404, {"error": "not_found"}, head)
                return
            self.send_content(200, *resource, head=head)

        def do_POST(self):
            if not self.permitted_host():
                self.json_response(403, {"error": "loopback_host_required"})
                return
            # A foreign page cannot scan files via a form, fetch, or DNS rebinding.
            origin = "http://127.0.0.1:" + str(self.server.server_port)
            if (
                self.headers.get("Origin") != origin
                or self.headers.get("X-AgentCost-Local") != "1"
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
            ):
                self.json_response(403, {"error": "same_origin_request_required"})
                return
            if urlsplit(self.path).path != "/api/refresh":
                self.json_response(404, {"error": "not_found"})
                return
            if collector is None:
                self.json_response(405, {"error": "static_report_cannot_refresh"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("invalid request size")
                self.connection.settimeout(5)
                changes = json.loads(self.rfile.read(size).decode("utf8"))
                accepted = snapshot.refresh(changes)
            except (ValueError, UnicodeError, TimeoutError):
                self.json_response(400, {"error": "invalid_source_settings_or_price_catalog"})
                return
            self.json_response(202 if accepted else 409, snapshot.status())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.snapshot = snapshot
    return server


def serve_report(report, port=8765, collector=None):
    with make_server(report, port, collector) as server:
        print("Dashboard: http://127.0.0.1:" + str(server.server_port) + "/", flush=True)
        print(
            "Local log refresh enabled." if collector else "Static report; refresh unavailable.",
            flush=True,
        )
        print("Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
