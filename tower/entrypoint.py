#!/usr/bin/env python3
"""Container entrypoint: render Asterisk configs from env vars, then run Asterisk + the screener.
If either process dies the container exits, and Docker's restart policy brings it back."""
import os
import re
import shutil
import signal
import subprocess
import sys
import time

TEMPLATE_DIR = os.environ.get("TEMPLATE_DIR", "/opt/callguard/asterisk")
AST_ETC = os.environ.get("AST_ETC", "/etc/asterisk")


def env(name, default=""):
    return os.environ.get(name, default).strip()


def render():
    cc_user, cc_pass = env("CC_USER"), env("CC_PASS")
    phone_user, phone_pass = env("PHONE_USER", "ly"), env("PHONE_PASS")
    if not phone_pass:
        print("WARNING: PHONE_PASS not set - Linphone can't register, so Connect will be off", flush=True)
        phone_pass = os.urandom(16).hex()
    ext = env("EXTERNAL_HOST")
    external = (f"external_media_address={ext}\nexternal_signaling_address={ext}") if ext else ""
    values = {
        "SIP_PORT": env("SIP_PORT", "5060"),
        "RTP_START": env("RTP_START", "10000"),
        "RTP_END": env("RTP_END", "10099"),
        "AGI_HOST": env("AGI_HOST", "127.0.0.1"),
        "AGI_PORT": env("AGI_PORT", "4573"),
        "CC_USER": cc_user, "CC_PASS": cc_pass,
        "PHONE_USER": phone_user, "PHONE_PASS": phone_pass,
        "EXTERNAL": external,
    }
    os.makedirs(AST_ETC, exist_ok=True)
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        src = os.path.join(TEMPLATE_DIR, name)
        if name.endswith(".tmpl"):
            text = open(src).read()
            if cc_user and cc_pass:
                text = text.replace("@@CALLCENTRIC_BEGIN@@\n", "").replace("@@CALLCENTRIC_END@@\n", "")
            else:
                text = re.sub(r"@@CALLCENTRIC_BEGIN@@.*?@@CALLCENTRIC_END@@\n", "", text, flags=re.S)
            for k, v in values.items():
                text = text.replace(f"@@{k}@@", v)
            leftover = re.findall(r"@@\w+@@", text)
            if leftover:
                sys.exit(f"Unfilled placeholders in {name}: {leftover}")
            with open(os.path.join(AST_ETC, name[:-5]), "w") as f:
                f.write(text)
        else:
            shutil.copy(src, os.path.join(AST_ETC, name))
    if not (cc_user and cc_pass):
        print("WARNING: CC_USER / CC_PASS not set - not registering with Callcentric", flush=True)


def prepare_data():
    """Asterisk (user 'asterisk') writes recordings; the app (root) reads them."""
    data = env("DATA_DIR", "/data")
    for sub in ("", "sounds", "recordings", "models"):
        os.makedirs(os.path.join(data, sub), exist_ok=True)
    shutil.chown(os.path.join(data, "recordings"), "asterisk", "asterisk")
    os.chmod(os.path.join(data, "recordings"), 0o775)


def main():
    render()
    prepare_data()
    procs = [subprocess.Popen([sys.executable, "-m", "app.main"], cwd="/opt/callguard")]
    time.sleep(2)  # let the app render prompts and open the AGI port first
    procs.append(subprocess.Popen(["asterisk", "-f", "-U", "asterisk", "-G", "asterisk"]))

    def stop(*_):
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        for p in procs:
            if p.poll() is not None:
                print(f"Process {p.args} exited with {p.returncode}; stopping container", flush=True)
                stop()
        time.sleep(2)


if __name__ == "__main__":
    main()
