"""Minimal FastAGI server: Asterisk opens a TCP connection per call and we drive it with AGI commands."""
import logging
import re
import socketserver

log = logging.getLogger("agi")


class Hangup(Exception):
    """The caller hung up."""


class AGI:
    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self.env = {}
        self.dead = False
        while True:
            line = self._readline()
            if not line:
                break
            k, _, v = line.partition(":")
            self.env[k.strip()] = v.strip()

    def _readline(self):
        raw = self.rfile.readline()
        if not raw:
            raise Hangup()
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    def cmd(self, line):
        """Send one AGI command, return (result:int, data:str). Raises Hangup on a dead channel."""
        if self.dead:
            raise Hangup()
        self.wfile.write((line + "\n").encode())
        self.wfile.flush()
        resp = self._readline()
        while resp == "HANGUP":                 # async hangup notice (agi_enhanced / SIGHUP)
            self.dead = True
            resp = self._readline()
        if resp.startswith("520-"):              # multi-line usage error
            while not resp.startswith("520 "):
                resp = self._readline()
            raise RuntimeError(f"AGI usage error for: {line}")
        if resp.startswith("511") or resp.startswith("HANGUP"):
            self.dead = True
            raise Hangup()
        m = re.match(r"200 result=(-?\d+)\s*(.*)", resp)
        if not m:
            raise RuntimeError(f"Unexpected AGI response {resp!r} to {line!r}")
        result, data = int(m.group(1)), m.group(2)
        if result == -1:
            self.dead = True
            raise Hangup()
        return result, data

    # ---- convenience wrappers
    def answer(self):
        self.cmd("ANSWER")

    def play(self, path, escape=""):
        """Returns the digit pressed (as a str) or ''."""
        r, _ = self.cmd(f'STREAM FILE {path} "{escape}"')
        return chr(r) if r > 0 else ""

    def wait_digit(self, ms):
        r, _ = self.cmd(f"WAIT FOR DIGIT {ms}")
        return chr(r) if r > 0 else ""

    def record(self, path_no_ext, max_ms, silence_s, beep=True, escape="#"):
        """Records to <path>.wav. Hangup during recording raises Hangup, but the file is kept."""
        opts = " BEEP" if beep else ""
        self.cmd(f'RECORD FILE {path_no_ext} wav "{escape}" {max_ms} 0{opts} s={silence_s}')

    def exec(self, app, args=""):
        r, _ = self.cmd(f'EXEC {app} "{args}"' if args else f"EXEC {app}")
        return r

    def get_var(self, expr):
        _, data = self.cmd(f"GET FULL VARIABLE {expr}")
        m = re.match(r"\((.*)\)", data)
        return m.group(1) if m else ""

    def wait(self, seconds):
        self.exec("Wait", str(seconds))

    def hangup(self):
        try:
            self.cmd("HANGUP")
        except Hangup:
            pass


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            agi = AGI(self.rfile, self.wfile)
        except Hangup:
            return
        try:
            self.server.call_handler(agi)
        except Hangup:
            pass
        except Exception:
            log.exception("Call handler crashed")


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, call_handler):
        self.call_handler = call_handler
        super().__init__(addr, _Handler)
