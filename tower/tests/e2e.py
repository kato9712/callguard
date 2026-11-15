#!/usr/bin/env python3
"""End-to-end test: real Asterisk + the screener + a fake Telegram server.

Each scenario places a call into the screening dialplan through a Local channel. The 'caller'
side plays synthetic phone-quality audio (espeak + telephone filter + line noise) and presses
keys like a person or a robocall would. Assertions check the call log and what was sent to Telegram.

Run as root on a machine with asterisk, espeak-ng, sox installed:
    python3 tests/e2e.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
TOWER = os.path.dirname(HERE)
DATA = "/tmp/cg-e2e"
TG_PORT = 18081
API_PORT = 18099
TOKEN = "TESTTOKEN"
API_TOKEN = "apitoken123"
CHAT = "424242"
HOLD = 12
AUDIO = os.path.join(DATA, "callers")

sys.path.insert(0, TOWER)

# ------------------------------------------------------------------ fake Telegram


class FakeTG:
    def __init__(self):
        self.sent = []          # (method, params)
        self.updates = []
        self.next_update = 1
        self.next_msg = 100
        self.lock = threading.Lock()

    def push_callback(self, msg_id, data):
        with self.lock:
            self.updates.append({"update_id": self.next_update, "callback_query": {
                "id": f"cb{self.next_update}", "data": data,
                "message": {"message_id": msg_id, "chat": {"id": int(CHAT)}}}})
            self.next_update += 1

    def push_text(self, text):
        with self.lock:
            self.updates.append({"update_id": self.next_update, "message": {
                "message_id": 1, "chat": {"id": int(CHAT)}, "text": text}})
            self.next_update += 1

    def find(self, method, since=0, contains=None):
        out = []
        for i, (m, p) in enumerate(self.sent):
            if i < since or m != method:
                continue
            blob = json.dumps(p)
            if contains and contains not in blob:
                continue
            out.append(p)
        return out


tg = FakeTG()


class TGHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        method = self.path.rsplit("/", 1)[-1]
        assert self.path.startswith(f"/bot{TOKEN}/"), self.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            params = json.loads(raw or b"{}")
        else:  # multipart: keep text fields + file sizes
            params = {"_multipart_bytes": len(raw)}
            for part in raw.split(b"--")[1:]:
                if b'name="' in part:
                    name = part.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
                    body = part.split(b"\r\n\r\n", 1)[-1].rstrip(b"\r\n")
                    params[name] = body.decode("utf-8", "replace") if b"filename=" not in part else f"<file {len(body)} bytes>"
        result = True
        if method == "getUpdates":
            off = params.get("offset", 0)
            deadline = time.time() + 1.0
            while time.time() < deadline:
                with tg.lock:
                    ups = [u for u in tg.updates if u["update_id"] >= off]
                if ups:
                    break
                time.sleep(0.1)
            result = ups
        else:
            with tg.lock:
                tg.sent.append((method, params))
            if method in ("sendMessage", "sendVoice", "sendDocument"):
                with tg.lock:
                    tg.next_msg += 1
                    result = {"message_id": tg.next_msg}
        body = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ------------------------------------------------------------------ caller audio

LINES = {
    "intro_tire": "Hi, this is Mike from Discount Tire. Your truck is ready for pick up whenever you get a chance.",
    "intro_warranty": "Hi, I'm calling about your vehicle's extended warranty. It's about to expire and we can lower your payments.",
    "intro_vague": "Hey, it's me, just call me back.",
    "robocall": ("This is an important message regarding your car's extended warranty. Our records indicate "
                 "that your factory warranty has expired. This is your final notice. Press one now to speak "
                 "with a warranty specialist. Press nine to be removed from our list."),
    "vm_tony": "Hey, it's Tony. Call me back when you get a chance about the boat this weekend. Talk to you later.",
    "vm_dentist": "Hello, this is Sarah from Bright Smiles Dental confirming your appointment Tuesday at nine. Please call us back.",
}


def make_audio():
    os.makedirs(AUDIO, exist_ok=True)
    for name, text in LINES.items():
        raw = os.path.join(AUDIO, f"{name}_raw.wav")
        out = os.path.join(AUDIO, f"{name}.wav")
        # A different voice than the prompts, then squeeze it into phone bandwidth and add line hiss.
        subprocess.run(["espeak-ng", "-v", "en-us+m3", "-s", "165", "-w", raw, text], check=True, capture_output=True)
        noise = os.path.join(AUDIO, f"{name}_noise.wav")
        dur = wav_seconds(raw)
        subprocess.run(["sox", "-n", "-r", "22050", "-c", "1", noise, "synth", f"{dur:.2f}", "pinknoise", "vol", "0.02"],
                       check=True, capture_output=True)
        subprocess.run(["sox", "-m", raw, noise, "-r", "8000", "-c", "1", "-b", "16", "-e", "signed-integer",
                        out, "highpass", "300", "lowpass", "3400", "norm", "-3"], check=True, capture_output=True)
        os.unlink(raw)
        os.unlink(noise)


def wav_seconds(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def prompt_len(name):
    return wav_seconds(os.path.join(DATA, "sounds", f"{name}.wav"))


def a(name):
    return os.path.join(AUDIO, name)


def hiss(seconds):
    """Line noise of a given length. Real calls send audio the whole time (never true silence),
    so simulated callers 'wait' by playing faint hiss instead of sending nothing."""
    ms = int(round(seconds * 1000))
    path = os.path.join(AUDIO, f"hiss_{ms}")
    if not os.path.exists(path + ".wav"):
        subprocess.run(["sox", "-n", "-r", "8000", "-c", "1", "-b", "16", "-e", "signed-integer", path + ".wav",
                        "synth", f"{ms / 1000:.3f}", "pinknoise", "vol", "0.01"], check=True, capture_output=True)
    return path


def W(seconds):
    return f"same => n,Playback({hiss(seconds)})"


# ------------------------------------------------------------------ dialplan for simulated callers

def write_test_dialplan():
    g = prompt_len("greet") + 0.8          # greeting + beep
    p5 = prompt_len("press_5")
    vm_known = prompt_len("vm_known") + 1.2
    vm_screen = prompt_len("vm_screen") + 1.2
    tire = wav_seconds(a("intro_tire.wav"))
    warr = wav_seconds(a("intro_warranty.wav"))
    vague = wav_seconds(a("intro_vague.wav"))

    def speak_then_press(intro, dur, digit, second_digit=None):
        t_press = 2.0 + 0.5 + p5     # recording stops after 2 s silence, then "press 5" plays
        lines = [W(g + 0.7), f"same => n,Playback({a(intro)})",
                 W(t_press), f"same => n,SendDTMF({digit})"]
        if second_digit:
            lines += [W(prompt_len('press_retry_5') + 0.5), f"same => n,SendDTMF({second_digit})"]
        return lines

    def wait_vm_then_speak(prompt_secs, vm):
        return ['same => n,While($["${GLOBAL(VMGO)}" != "1"])', W(0.3), "same => n,EndWhile()",
                W(prompt_secs), f"same => n,Playback({a(vm)})", W(2), "same => n,Hangup()"]

    ext = {
        # Real person with a real reason, then waits on hold for your decision. VMGO lets them leave a message.
        "human_tire": speak_then_press("intro_tire", tire, 5) + wait_vm_then_speak(vm_screen, "vm_tony"),
        # Telemarketer who is a live person (passes the keypad check).
        "human_warranty": speak_then_press("intro_warranty", warr, 5) + [W(60)],
        # Vague real person.
        "human_vague": speak_then_press("intro_vague", vague, 5) + wait_vm_then_speak(vm_screen, "vm_tony"),
        # Prerecorded robocall: starts talking immediately, never presses keys, then hangs up.
        "robocall": [f"same => n,Playback({a('robocall')})", f"same => n,Playback({a('robocall')})", "same => n,Hangup()"],
        # Dead air (autodialer waiting for "hello").
        "silent": [W(45)],
        # Speaks, but presses the wrong key twice.
        "wrong_key": speak_then_press("intro_tire", tire, 3, second_digit=3) + [W(20)],
        # Hangs up during the greeting.
        "hangup_early": [W(3), "same => n,Hangup()"],
        # Known contact you didn't pick up: leaves a voicemail.
        "known_vm": [W(vm_known), f"same => n,Playback({a('vm_dentist')})", W(1), "same => n,Hangup()"],
        # Known contact who hangs up without a message.
        "known_novm": [W(vm_known), "same => n,Hangup()"],
        # Blocked number.
        "blocked": [W(15)],
    }
    out = ["; generated by tests/e2e.py", "[sim-entry]",
           "exten => _X.,1,Set(CALLERID(num)=${EXTEN})", " same => n,Goto(screen,s,1)",
           "exten => anon,1,Set(CALLERID(num)=)", " same => n,Set(CALLERID(name)=)", " same => n,Goto(screen,s,1)", "",
           "[test-callers]"]
    for name, steps in ext.items():
        out.append(f"exten => {name},1,Answer()")
        out += [" " + s for s in steps]
        out.append(" same => n,Hangup()")
    out += ["", "; stands in for your phone (Linphone). PHONEMODE=answer|noanswer",
            "[test-phone]",
            'exten => phone,1,GotoIf($["${GLOBAL(PHONEMODE)}" = "noanswer"]?nope)',
            " same => n,Answer()", " same => n,Wait(3)", " same => n,Hangup()",
            " same => n(nope),Wait(60)"]
    with open("/etc/asterisk/extensions_test.conf", "w") as f:
        f.write("\n".join(out) + "\n")


# ------------------------------------------------------------------ helpers

def ast(cmd):
    return subprocess.run(["asterisk", "-rx", cmd], capture_output=True, text=True).stdout


def place_call(number, sim):
    chan = f"Local/{number}@sim-entry" if number else "Local/anon@sim-entry"
    body = f"Channel: {chan}\nContext: test-callers\nExtension: {sim}\nPriority: 1\nMaxRetries: 0\nWaitTime: 30\n"
    tmp = f"/tmp/cg-call-{time.time_ns()}.call"
    with open(tmp, "w") as f:
        f.write(body)
    os.rename(tmp, f"/var/spool/asterisk/outgoing/{os.path.basename(tmp)}")


def db():
    c = sqlite3.connect(os.path.join(DATA, "callguard.db"))
    c.row_factory = sqlite3.Row
    return c


def last_call():
    return dict(db().execute("SELECT * FROM calls ORDER BY id DESC LIMIT 1").fetchone() or {})


def wait_for(fn, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {what}")


def call_done(prev_id):
    def f():
        c = last_call()
        return c if c.get("id", 0) > prev_id and c["outcome"] != "in_progress" else None
    return f


def channels_idle():
    return "0 active channels" in ast("core show channels count")


def api(method, path, body=None, token=API_TOKEN):
    req = Request(f"http://127.0.0.1:{API_PORT}{path}", method=method,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except Exception as e:
        return getattr(e, "code", 0), {}


# ------------------------------------------------------------------ scenarios

RESULTS = []
WHISPER = os.environ.get("EXPECT_WHISPER") == "1"   # CI runs with Whisper; local sandbox may only have pocketsphinx


def scenario(fn):
    def run():
        ast("dialplan set global VMGO 0")
        ast("dialplan set global PHONEMODE answer")
        wait_for(channels_idle, 60, "previous call to end")
        prev = last_call().get("id", 0)
        mark = len(tg.sent)
        t0 = time.time()
        try:
            detail = fn(prev, mark) or ""
            RESULTS.append((fn.__name__, True, f"{time.time() - t0:.0f}s {detail}"))
            print(f"PASS {fn.__name__} ({time.time() - t0:.0f}s) {detail}", flush=True)
        except Exception as e:
            RESULTS.append((fn.__name__, False, str(e)))
            print(f"FAIL {fn.__name__}: {e}", flush=True)
            print("   last call:", last_call(), flush=True)
    run.__name__ = fn.__name__
    return run


def alert_for(mark, timeout=40):
    return wait_for(lambda: (tg.find("sendMessage", mark, "Screened call") or [None])[0], timeout, "Telegram alert")


def msg_id_of(alert):
    # the fake server numbers messages sequentially; find the id from the call log instead
    return last_call().get("tg_msg_id")


@scenario
def robocall_is_dropped(prev, mark):
    place_call("8885550199", "robocall")
    c = wait_for(call_done(prev), 90, "robocall to finish")
    assert c["outcome"] in ("failed_keypad", "hung_up_early"), c["outcome"]
    assert not tg.find("sendMessage", mark, "Screened call"), "robocall should not reach Telegram"
    return c["outcome"]


@scenario
def silent_autodialer_is_dropped(prev, mark):
    place_call("2125550142", "silent")
    c = wait_for(call_done(prev), 60, "silent call to finish")
    assert c["outcome"] == "silent", c["outcome"]
    assert not tg.find("sendMessage", mark, "Screened call")


@scenario
def wrong_key_is_dropped(prev, mark):
    place_call("9185550111", "wrong_key")
    c = wait_for(call_done(prev), 60, "wrong-key call to finish")
    assert c["outcome"] == "failed_keypad", c["outcome"]
    assert not tg.find("sendMessage", mark, "Screened call")


@scenario
def hangup_during_greeting(prev, mark):
    place_call("4055550123", "hangup_early")
    c = wait_for(call_done(prev), 30, "early hangup")
    assert c["outcome"] == "hung_up_early", c["outcome"]
    assert not tg.find("sendMessage", mark, "Screened call")


@scenario
def real_caller_connect(prev, mark):
    place_call("4055550100", "human_tire")
    alert = alert_for(mark)
    assert "(405) 555-0100" in alert["text"], alert["text"]
    buttons = [b["callback_data"].split(":")[0] for row in alert["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == ["connect", "message", "block", "allow"], buttons
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"connect:{cid}")
    c = wait_for(call_done(prev), 40, "connect")
    assert c["outcome"] == "connected", c["outcome"]
    wait_for(lambda: tg.find("sendMessage", mark, "Connected"), 10, "result note")
    if WHISPER:
        assert "tire" in c["transcript"].lower(), c["transcript"]
        assert "Discount Tire" in alert["text"] or "discount tire" in alert["text"].lower(), alert["text"]
    return f"transcript={c['transcript']!r} verdict={c['verdict']}"


@scenario
def real_caller_take_message(prev, mark):
    place_call("4055550101", "human_tire")
    alert = alert_for(mark)
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"message:{cid}")
    time.sleep(1.0)
    ast("dialplan set global VMGO 1")
    c = wait_for(call_done(prev), 60, "voicemail")
    assert c["outcome"] == "voicemail", c["outcome"]
    v = wait_for(lambda: tg.find("sendVoice", mark), 30, "voicemail sent to Telegram")[0]
    assert "<file" in v.get("voice", ""), v
    assert "Voicemail from (405) 555-0101" in v["caption"], v["caption"]
    assert "Block" in v.get("reply_markup", ""), "unknown caller voicemail should have Block/Allow buttons"
    if WHISPER:
        assert "boat" in v["caption"].lower(), v["caption"]
    return f"vm caption={v['caption'][:90]!r}"


@scenario
def connect_but_phone_doesnt_answer(prev, mark):
    ast("dialplan set global PHONEMODE noanswer")
    place_call("4055550102", "human_tire")
    alert = alert_for(mark)
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"connect:{cid}")
    # Ring time (RING_SECONDS=6 in this test) then the vm prompt.
    time.sleep(6 + 1)
    ast("dialplan set global VMGO 1")
    c = wait_for(call_done(prev), 60, "fallback to voicemail")
    assert c["outcome"] == "voicemail", c["outcome"]


@scenario
def no_tap_takes_message(prev, mark):
    place_call("4055550103", "human_vague")
    alert_for(mark)
    time.sleep(HOLD + 1)
    ast("dialplan set global VMGO 1")
    c = wait_for(call_done(prev), 60, "timeout voicemail")
    assert c["outcome"] == "voicemail", c["outcome"]


@scenario
def telemarketer_flagged_then_blocked(prev, mark):
    place_call("8005550177", "human_warranty")
    alert = alert_for(mark)
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"block:{cid}")
    c = wait_for(call_done(prev), 30, "block")
    assert c["outcome"] == "blocked", c["outcome"]
    s, lists = api("GET", "/api/lists")
    assert "8005550177" in lists["block"], lists
    if WHISPER:
        assert c["verdict"] == "spam", (c["verdict"], c["transcript"])
        assert "Likely sales/scam" in alert["text"], alert["text"]
    return f"verdict={c['verdict']} transcript={c['transcript']!r}"


@scenario
def blocked_number_hung_up(prev, mark):
    place_call("8005550177", "blocked")
    c = wait_for(call_done(prev), 30, "blocked call")
    assert c["outcome"] == "blocked_list", c["outcome"]
    assert not tg.find("sendMessage", mark, "Screened call")


@scenario
def always_allow_connects_and_lists(prev, mark):
    place_call("5805550150", "human_tire")
    alert = alert_for(mark)
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"allow:{cid}")
    c = wait_for(call_done(prev), 40, "allow+connect")
    assert c["outcome"] == "connected", c["outcome"]
    s, lists = api("GET", "/api/lists")
    assert "5805550150" in lists["allow"], lists


@scenario
def allowed_number_gets_voicemail_not_screening(prev, mark):
    # Allowed numbers normally ring your phone; if you miss it they land here as plain voicemail.
    place_call("5805550150", "known_vm")
    c = wait_for(call_done(prev), 60, "known voicemail")
    assert c["flow"] == "voicemail" and c["outcome"] == "voicemail", (c["flow"], c["outcome"])
    v = wait_for(lambda: tg.find("sendVoice", mark), 30, "voicemail to Telegram")[0]
    assert "reply_markup" not in v, "known caller voicemail shouldn't have Block/Allow buttons"


@scenario
def contact_hash_voicemail(prev, mark):
    import hashlib
    h = hashlib.sha256(b"9405550160").hexdigest()
    s, r = api("PUT", "/api/known", {"hashes": [h, "not-a-hash"]})
    assert s == 200 and r["count"] == 1, (s, r)
    place_call("19405550160", "known_vm")      # +1 prefix must still match
    c = wait_for(call_done(prev), 60, "contact voicemail")
    assert c["flow"] == "voicemail" and c["outcome"] == "voicemail", (c["flow"], c["outcome"])
    v = wait_for(lambda: tg.find("sendVoice", mark), 30, "voicemail to Telegram")[0]
    if WHISPER:
        assert "appointment" in v["caption"].lower(), v["caption"]
    return f"transcript caption={v['caption'][:100]!r}"


@scenario
def contact_hangs_up_without_message(prev, mark):
    place_call("9405550160", "known_novm")
    c = wait_for(call_done(prev), 60, "no-message")
    assert c["outcome"] == "no_message", c["outcome"]
    time.sleep(2)
    assert not tg.find("sendVoice", mark), "nothing should be sent"


@scenario
def anonymous_caller_is_screened(prev, mark):
    place_call("", "human_tire")
    alert = alert_for(mark)
    assert "No caller ID" in alert["text"], alert["text"]
    cid = last_call()["id"]
    tg.push_callback(msg_id_of(alert), f"connect:{cid}")
    c = wait_for(call_done(prev), 40, "anon connect")
    assert c["outcome"] == "connected", c["outcome"]


@scenario
def api_rejects_bad_token(prev, mark):
    s, _ = api("GET", "/api/lists", token="wrong")
    assert s == 401, s
    s, _ = api("PUT", "/api/known", {"hashes": []}, token="")
    assert s == 401, s
    s, _ = api("PUT", "/api/known", {"hashes": "nope"})
    assert s == 400, s


@scenario
def telegram_commands(prev, mark):
    tg.push_text("/allow (918) 555-0199")
    wait_for(lambda: tg.find("sendMessage", mark, "Allowed (918) 555-0199"), 10, "/allow reply")
    tg.push_text("/block 918-555-0199")
    wait_for(lambda: tg.find("sendMessage", mark, "Blocked (918) 555-0199"), 10, "/block reply")
    s, lists = api("GET", "/api/lists")
    assert "9185550199" in lists["block"] and "9185550199" not in lists["allow"], lists
    tg.push_text("/unblock 9185550199")
    wait_for(lambda: tg.find("sendMessage", mark, "Removed"), 10, "/unblock reply")
    tg.push_text("/recent")
    wait_for(lambda: tg.find("sendMessage", mark, "connected"), 10, "/recent reply")
    tg.push_text("/lists")
    wait_for(lambda: tg.find("sendMessage", mark, "Always allow:"), 10, "/lists reply")


@scenario
def late_tap_after_hangup_is_harmless(prev, mark):
    old = db().execute("SELECT id, tg_msg_id FROM calls WHERE outcome='connected' LIMIT 1").fetchone()
    tg.push_callback(old["tg_msg_id"], f"block:{old['id']}")
    wait_for(lambda: tg.find("answerCallbackQuery", mark, "already hung up"), 10, "late-tap answer")
    s, lists = api("GET", "/api/lists")
    # the late tap must not block the number
    assert db().execute("SELECT number FROM calls WHERE id=?", (old["id"],)).fetchone()[0] not in lists["block"]


# ------------------------------------------------------------------ main

def main():
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA)
    srv = ThreadingHTTPServer(("127.0.0.1", TG_PORT), TGHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ, DATA_DIR=DATA, TG_TOKEN=TOKEN, TG_CHAT_ID=CHAT, TG_API=f"http://127.0.0.1:{TG_PORT}",
               API_TOKEN=API_TOKEN, API_PORT=str(API_PORT), HOLD_SECONDS=str(HOLD), RING_SECONDS="6",
               SIP_PHONE="Local/phone@test-phone", FIXED_CHALLENGE_DIGIT="5",
               STT_BACKEND=os.environ.get("STT_BACKEND", "auto"), PHONE_PASS="testpass",
               TEMPLATE_DIR=os.path.join(TOWER, "asterisk"), AST_ETC="/etc/asterisk")
    os.environ.update(env)
    import entrypoint
    entrypoint.render()
    os.makedirs(os.path.join(DATA, "recordings"), exist_ok=True)

    app = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=TOWER, env=env,
                           stdout=open(os.path.join(DATA, "app.log"), "w"), stderr=subprocess.STDOUT)
    wait_for(lambda: os.path.exists(os.path.join(DATA, "sounds", ".stamp")), 120, "prompts rendered")
    make_audio()
    write_test_dialplan()
    subprocess.run(["pkill", "-f", "asterisk -f"], capture_output=True)
    time.sleep(1)
    astp = subprocess.Popen(["asterisk", "-f"], stdout=open(os.path.join(DATA, "asterisk.log"), "w"),
                            stderr=subprocess.STDOUT)
    wait_for(lambda: "Asterisk Ready" in open(os.path.join(DATA, "asterisk.log")).read()
             or "Fully Booted" in ast("core show uptime") or ast("core show version").startswith("Asterisk"),
             60, "asterisk up")
    time.sleep(3)
    wait_for(lambda: api("GET", "/health", token="")[0] == 200, 30, "app API up")
    print("Asterisk:", ast("core show version").strip())
    print("PJSIP endpoints:", " ".join(l.split()[1] for l in ast("pjsip show endpoints").splitlines()
                                       if l.strip().startswith("Endpoint:") and "<Endpoint" not in l))
    try:
        for s in [robocall_is_dropped, silent_autodialer_is_dropped, wrong_key_is_dropped, hangup_during_greeting,
                  real_caller_connect, real_caller_take_message, connect_but_phone_doesnt_answer,
                  no_tap_takes_message, telemarketer_flagged_then_blocked, blocked_number_hung_up,
                  always_allow_connects_and_lists, allowed_number_gets_voicemail_not_screening,
                  contact_hash_voicemail, contact_hangs_up_without_message, anonymous_caller_is_screened,
                  api_rejects_bad_token, telegram_commands, late_tap_after_hangup_is_harmless]:
            s()
    finally:
        astp.terminate()
        app.terminate()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} scenarios passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
