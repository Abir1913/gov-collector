"""Tiny control server so the website's admin button can start a crawl on a machine you run (a VPS, a home server).
  GOV_ADMIN_TOKEN=secret python -m govcollect serve --port 8787
The site calls POST /crawl with header X-Admin-Token and polls GET /status. Put it behind HTTPS (Caddy/nginx)."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .jobs import LOCAL, run_job


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", os.getenv("GOV_ALLOW_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.startswith("/status"):
            return self._send(200, LOCAL)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/crawl":
            return self._send(404, {"error": "not found"})
        if self.headers.get("X-Admin-Token") != os.environ["GOV_ADMIN_TOKEN"]:
            return self._send(401, {"error": "bad token"})
        if LOCAL["state"] == "running":
            return self._send(409, {"error": "a crawl is already running"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        scope = body.get("scope", "full")
        threading.Thread(target=run_job, args=(scope,), daemon=True).start()
        self._send(202, {"started": True, "scope": scope})

    def log_message(self, *a):
        pass


def serve(port=8787):
    if not os.getenv("GOV_ADMIN_TOKEN"):
        raise SystemExit("Set GOV_ADMIN_TOKEN before starting the server.")
    print(f"control server on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
