"""What happens on each forwarded call."""
import logging
import os
import random
import threading
import time

from . import classify, config, db, speech, telegram
from .agi import Hangup

log = logging.getLogger("flow")

ONLINE_STATES = {"NOT_INUSE", "INUSE", "RINGING", "RINGINUSE", "ONHOLD", "BUSY"}

OUTCOME_NOTE = {
    "connected": "📲 Connected — you took the call",
    "voicemail": "📩 They left a message",
    "no_message": "📴 They hung up without leaving a message",
    "blocked": "🚫 Blocked and hung up",
    "hung_up_hold": "📴 They hung up while on hold",
}


class Call:
    def __init__(self, agi):
        self.agi = agi
        raw = agi.env.get("agi_callerid", "")
        self.number = "" if raw.lower() in ("unknown", "anonymous", "restricted", "") else raw
        self.call_id = None
        self.msg_id = None
        self.outcome = None

    # ------------------------------------------------------------ helpers
    def p(self, name, escape=""):
        return self.agi.play(speech.sound(name), escape)

    def rec_path(self, kind):
        os.makedirs(config.REC_DIR, exist_ok=True)
        return os.path.join(config.REC_DIR, f"{int(time.time())}_{self.call_id}_{kind}")

    def phone_online(self):
        try:
            return self.agi.get_var("${DEVICE_STATE(%s)}" % config.SIP_PHONE) in ONLINE_STATES
        except Hangup:
            raise
        except Exception:
            return False

    def end(self, outcome):
        self.outcome = outcome
        db.update_call(self.call_id, outcome=outcome)

    # ------------------------------------------------------------ entry
    def run(self):
        log.info("Incoming call from %r", self.number or "anonymous")
        self.agi.answer()
        self.agi.wait(0.5)
        if db.in_list(self.number, "block"):
            self.call_id = db.new_call(self.number, "blocked")
            try:
                self.p("blocked")
            finally:
                self.end("blocked_list")
            self.agi.hangup()
            return
        if db.is_known(self.number):
            # A contact you didn't pick up (or your line was busy): plain voicemail, no screening.
            self.call_id = db.new_call(self.number, "voicemail")
            self.voicemail("vm_known", known=True)
        else:
            self.call_id = db.new_call(self.number, "screen")
            try:
                self.screen()
            finally:
                if self.outcome is None:
                    self.end("hung_up_hold" if self.msg_id else "hung_up_early")
                if self.msg_id:
                    telegram.finish_alert(self.msg_id, OUTCOME_NOTE.get(self.outcome, self.outcome))
        self.agi.hangup()

    # ------------------------------------------------------------ screening
    def screen(self):
        # 1) Ask for name + reason. Robocalls usually don't answer, or talk over the prompt.
        intro = self.rec_path("intro")
        spoke = 0.0
        for attempt in range(2):
            self.p("greet" if attempt == 0 else "greet_again")
            self.agi.record(intro, config.INTRO_MAX_SECONDS * 1000, silence_s=2)
            spoke = speech.speech_seconds(intro + ".wav")
            if spoke >= 0.6:
                break
        if spoke < 0.6:
            self.end("silent")
            self.p("goodbye")
            return

        # Start transcribing while the caller does the keypad check.
        result = {}
        worker = threading.Thread(target=lambda: result.update(text=speech.transcribe(intro + ".wav")),
                                  daemon=True)
        worker.start()

        # 2) Keypad check: a random digit. Autodialers and recordings can't follow it.
        digit = config.FIXED_CHALLENGE_DIGIT or str(random.randint(1, 9))
        pressed = self.p(f"press_{digit}", "0123456789*#") or self.agi.wait_digit(5000)
        if pressed != digit:
            pressed = self.p(f"press_retry_{digit}", "0123456789*#") or self.agi.wait_digit(6000)
        if pressed != digit:
            worker.join(timeout=10)
            db.update_call(self.call_id, transcript=result.get("text", ""))
            self.end("failed_keypad")
            self.p("goodbye")
            return

        # 3) Real person. Put them on hold and ask you on Telegram.
        self.p("hold")
        worker.join(timeout=12)
        transcript = result.get("text", "")
        verdict, reason = classify.classify(transcript)
        db.update_call(self.call_id, transcript=transcript, verdict=verdict, reason=reason)
        online = self.phone_online()
        self.msg_id = telegram.screen_alert(self.call_id, self.number, transcript, verdict, reason, online)
        if self.msg_id:
            db.update_call(self.call_id, tg_msg_id=self.msg_id)

        action = None
        if self.msg_id:
            start = time.time()
            last_prompt = start
            while time.time() - start < config.HOLD_SECONDS:
                action = db.get_decision(self.call_id)
                if action:
                    break
                if time.time() - last_prompt >= 15:
                    self.p("still_holding")
                    last_prompt = time.time()
                else:
                    self.agi.wait(1)
        # No Telegram, or no answer from you in time -> take a message.
        if action is None:
            action = db.set_decision(self.call_id, "message")

        if action == "block":
            self.end("blocked")
            self.p("blocked")
            return
        if action in ("connect", "allow"):
            if self.phone_online():
                self.p("connecting")
                # 'g' = come back here if the call wasn't answered. When your phone picks up, the
                # dialplan touches a marker file, so we know it connected even if the caller hangs up first.
                marker = os.path.join(config.REC_DIR, f"answered_{self.call_id}")
                self.agi.cmd(f"SET VARIABLE _CGMARK {marker}")
                try:
                    self.agi.exec("Dial", f"{config.SIP_PHONE},{config.RING_SECONDS},gU(cg-answered)")
                    status = self.agi.get_var("${DIALSTATUS}")
                except Hangup:
                    if os.path.exists(marker):
                        self.end("connected")
                    raise
                if status == "ANSWER" or os.path.exists(marker):
                    self.end("connected")
                    return
        self.voicemail("vm_screen", known=False)

    # ------------------------------------------------------------ voicemail
    def voicemail(self, prompt, known):
        path = self.rec_path("vm")
        self.p(prompt)
        hung_up = False
        try:
            self.agi.record(path, config.VOICEMAIL_MAX_SECONDS * 1000, silence_s=6)
        except Hangup:
            hung_up = True          # normal: people hang up when they're done talking
        wav = path + ".wav"
        if speech.speech_seconds(wav) < 0.5:
            self.end("no_message")
            if hung_up:
                raise Hangup()
            return
        db.update_call(self.call_id, voicemail=wav)
        self.end("voicemail")
        if not hung_up:
            try:
                self.p("msg_sent")
            except Hangup:
                pass
        # Transcribe + send after the caller is gone, off the call thread.
        threading.Thread(target=self._send_voicemail, args=(wav, known), daemon=True).start()
        if hung_up:
            raise Hangup()

    def _send_voicemail(self, wav, known):
        text = speech.transcribe(wav)
        db.update_call(self.call_id, transcript=((db.get_call(self.call_id) or {}).get("transcript") or "")
                       + (" | VM: " + text if text else ""))
        ogg = None
        try:
            ogg = speech.to_ogg(wav)
        except Exception as e:
            log.warning("OGG conversion failed: %s", e)
        telegram.voicemail(self.number, ogg, wav, text, known, reply_to=self.msg_id)


def handle(agi):
    Call(agi).run()
