"""Serve only generated aggregates on the local loopback interface."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def make_server(report, port=8765):
    report = Path(report).resolve()
    # Read an immutable snapshot; do not map URLs to filesystem paths.
    resources = {
        "/": ("text/html; charset=utf-8", report.read_bytes()),
        "/report.html": ("text/html; charset=utf-8", report.read_bytes()),
        "/usage.json": (
            "application/json; charset=utf-8",
            report.with_name("usage.json").read_bytes(),
        ),
        "/healthz": ("application/json", b'{"status":"ok"}\n'),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def respond(self, head):
            if self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port):
                self.send_error(403, "Loopback Host required")
                return
            resource = resources.get(urlsplit(self.path).path)
            if resource is None:
                self.send_error(404)
                return
            mime, content = resource
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if not head:
                self.wfile.write(content)

        def log_message(self, format, *args):
            # No paths, labels or payloads in access logs.
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_report(report, port=8765):
    with make_server(report, port) as server:
        print("Dashboard: http://127.0.0.1:" + str(server.server_port) + "/", flush=True)
        print("Static snapshot. Press Ctrl+C to stop; restart to refresh.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
