"""Small HTTP API for the Android app. Keep it on your LAN / Tailscale (don't proxy it publicly).

GET  /health                      -> {"ok": true}
GET  /api/lists                   -> {"allow": [...], "block": [...]}   (10-digit numbers)
PUT  /api/known  {"hashes": [...]} -> {"count": n}    sha256 of each contact's 10-digit number
All /api/* calls need:  Authorization: Bearer <API_TOKEN>
"""
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db

log = logging.getLogger("api")


class Handler(BaseHTTPRequestHandler):
    server_version = "CallGuard"

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not config.API_TOKEN:
            return False
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {config.API_TOKEN}".encode())

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if not self._authed():
            return self._send(401, {"error": "bad token"})
        if self.path == "/api/lists":
            return self._send(200, {"allow": db.get_list("allow"), "block": db.get_list("block")})
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        if not self._authed():
            return self._send(401, {"error": "bad token"})
        if self.path != "/api/known":
            return self._send(404, {"error": "not found"})
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 5_000_000)
            data = json.loads(self.rfile.read(length) or b"{}")
            hashes = data.get("hashes", [])
            if not isinstance(hashes, list):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "expected {\"hashes\": [...]}"})
        self._send(200, {"count": db.replace_known(hashes)})


def start():
    if not config.API_TOKEN:
        log.warning("API_TOKEN not set - phone app sync is disabled")
    srv = ThreadingHTTPServer(("0.0.0.0", config.API_PORT), Handler)
    threading.Thread(target=srv.serve_forever, name="api", daemon=True).start()
    return srv
