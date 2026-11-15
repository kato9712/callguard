"""Optional LLM path: used when reachable, silently falls back to rules when the PC is off."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", "/tmp/cg-unit")
from app import classify, config  # noqa: E402


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["messages"][0]["content"].count("Caller said") == 1
        reply = 'Sure! {"verdict": "ok", "summary": "Tire shop, truck ready"}'
        out = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def test_llm():
    srv = HTTPServer(("127.0.0.1", 18555), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    config.LLM_URL = "http://127.0.0.1:18555"
    assert classify.classify("hi this is mike from discount tire") == ("ok", "Tire shop, truck ready")
    srv.shutdown()
    srv.server_close()
    # PC off: connection refused -> rules
    config.LLM_URL = "http://127.0.0.1:18556"
    assert classify.classify("calling about your extended warranty")[0] == "spam"
    # Empty transcript never calls the LLM
    assert classify.classify("")[0] == "unclear"
    print("llm ok")


if __name__ == "__main__":
    test_llm()
