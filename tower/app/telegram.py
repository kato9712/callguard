"""Telegram alerts with Connect / Take message / Block / Always allow buttons."""
import html
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import uuid

from . import config, db

log = logging.getLogger("telegram")

VERDICT_LABEL = {
    "spam": "⚠️ Likely sales/scam",
    "ok": "✅ Sounds legit",
    "unclear": "❔ Unclear",
}


def enabled():
    return bool(config.TG_TOKEN and config.TG_CHAT_ID)


def _call(method, params=None, files=None, timeout=15):
    url = f"{config.TG_API}/bot{config.TG_TOKEN}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (params or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fname, data, ctype) in files.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def safe(method, params=None, files=None):
    if not enabled():
        return None
    try:
        return _call(method, params, files)
    except Exception as e:
        log.warning("Telegram %s failed: %s", method, e)
        return None


def _esc(s):
    return html.escape(s or "", quote=False)


def screen_alert(call_id, number, transcript, verdict, reason, phone_online):
    t = transcript.strip() if transcript else ""
    said = f"“{_esc(t)}”" if t else "<i>(couldn't make out words — tap Take message to hear them)</i>"
    text = (f"📞 <b>Screened call</b> from <b>{_esc(db.pretty(number))}</b>\n"
            f"{said}\n"
            f"{VERDICT_LABEL.get(verdict, '❔')} · {_esc(reason)}\n"
            f"<i>Caller is on hold for {config.HOLD_SECONDS}s</i>")
    row1 = []
    if phone_online:
        row1.append({"text": "📲 Connect", "callback_data": f"connect:{call_id}"})
    row1.append({"text": "📩 Take message", "callback_data": f"message:{call_id}"})
    row2 = [{"text": "🚫 Block", "callback_data": f"block:{call_id}"},
            {"text": "⭐ Always allow", "callback_data": f"allow:{call_id}"}]
    if not phone_online:
        text += "\n<i>Linphone isn't connected, so Connect is off.</i>"
    res = safe("sendMessage", {"chat_id": config.TG_CHAT_ID, "text": text, "parse_mode": "HTML",
                               "reply_markup": {"inline_keyboard": [row1, row2]}})
    return res["message_id"] if res else None


def finish_alert(msg_id, note):
    """Remove the buttons and add the result line."""
    if not msg_id:
        return
    safe("editMessageReplyMarkup", {"chat_id": config.TG_CHAT_ID, "message_id": msg_id,
                                    "reply_markup": {"inline_keyboard": []}})
    safe("sendMessage", {"chat_id": config.TG_CHAT_ID, "text": note,
                         "reply_to_message_id": msg_id, "allow_sending_without_reply": True})


def voicemail(number, ogg_path, wav_path, transcript, known, reply_to=None):
    caption = f"📩 Voicemail from {db.pretty(number)}"
    if transcript:
        caption += f"\n“{transcript}”"
    caption = caption[:1000]
    kb = None
    n = db.normalize(number)
    if n and not known:
        kb = {"inline_keyboard": [[{"text": "🚫 Block", "callback_data": f"blocknum:{n}"},
                                   {"text": "⭐ Always allow", "callback_data": f"allownum:{n}"}]]}
    params = {"chat_id": config.TG_CHAT_ID, "caption": caption}
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = "true"
    if kb:
        params["reply_markup"] = json.dumps(kb)
    if ogg_path:
        res = safe("sendVoice", params, {"voice": ("voicemail.ogg", open(ogg_path, "rb").read(), "audio/ogg")})
    else:
        res = safe("sendDocument", params, {"document": ("voicemail.wav", open(wav_path, "rb").read(), "audio/wav")})
    if res is None and enabled():  # file upload failed - at least send the text
        safe("sendMessage", {"chat_id": config.TG_CHAT_ID, "text": caption})


# ---------------------------------------------------------------- incoming taps & commands

ACTION_NOTE = {
    "connect": "📲 Connecting…",
    "message": "📩 Taking a message…",
    "block": "🚫 Blocked",
    "allow": "⭐ Allowed — connecting",
}

HELP = ("Commands:\n/recent — last 10 calls\n/allow 4055550100 — always let a number ring\n"
        "/block 4055550100 — always hang up on a number\n/unblock 4055550100 — remove from both lists\n"
        "/lists — show allow and block lists")


def _handle_callback(cb):
    data = cb.get("data", "")
    action, _, arg = data.partition(":")
    answer = "Done"
    if action in ("connect", "message", "block", "allow") and arg.isdigit():
        call_id = int(arg)
        call = db.get_call(call_id)
        if call and call["outcome"] == "in_progress":
            chosen = db.set_decision(call_id, action)
            if action in ("block", "allow"):
                db.set_list(call["number"], action)
            answer = ACTION_NOTE.get(chosen, "Done")
        else:
            answer = "That caller already hung up"
    elif action in ("blocknum", "allownum") and arg.isdigit():
        kind = action[:-3]
        db.set_list(arg, kind)
        answer = f"{'Blocked' if kind == 'block' else 'Allowed'} {db.pretty(arg)}"
        msg = cb.get("message") or {}
        if msg.get("message_id"):
            safe("editMessageReplyMarkup", {"chat_id": config.TG_CHAT_ID, "message_id": msg["message_id"],
                                            "reply_markup": {"inline_keyboard": []}})
    safe("answerCallbackQuery", {"callback_query_id": cb["id"], "text": answer})


def _handle_command(text):
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower() if parts else ""
    arg = " ".join(parts[1:])
    if cmd == "/recent":
        rows = db.recent_calls(10)
        if not rows:
            return "No calls yet."
        lines = []
        for r in rows:
            when = time.strftime("%b %d %I:%M %p", time.localtime(r["ts"]))
            lines.append(f"{when} · {db.pretty(r['number'])} · {r['outcome']}")
        return "\n".join(lines)
    if cmd in ("/allow", "/block"):
        if db.set_list(arg, cmd[1:]):
            return f"{'Allowed' if cmd == '/allow' else 'Blocked'} {db.pretty(arg)}. The phone app picks this up on its next sync."
        return "Send it like: " + cmd + " 4055550100"
    if cmd == "/unblock":
        db.remove_from_lists(arg)
        return f"Removed {db.pretty(arg)} from the lists."
    if cmd == "/lists":
        a = ", ".join(db.pretty(n) for n in db.get_list("allow")) or "none"
        b = ", ".join(db.pretty(n) for n in db.get_list("block")) or "none"
        return f"Always allow: {a}\nBlocked: {b}"
    return HELP


def poll_forever():
    if not enabled():
        log.warning("TG_TOKEN / TG_CHAT_ID not set - Telegram alerts are off")
        return
    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": ["callback_query", "message"]}
            if offset is not None:
                params["offset"] = offset
            updates = _call("getUpdates", params, timeout=35)
            for u in updates:
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    cb = u["callback_query"]
                    chat = str((cb.get("message") or {}).get("chat", {}).get("id", ""))
                    if chat == str(config.TG_CHAT_ID):
                        _handle_callback(cb)
                elif "message" in u:
                    m = u["message"]
                    if str(m.get("chat", {}).get("id")) == str(config.TG_CHAT_ID) and m.get("text", "").startswith("/"):
                        safe("sendMessage", {"chat_id": config.TG_CHAT_ID, "text": _handle_command(m["text"])})
        except Exception as e:
            log.warning("Telegram poll error: %s", e)
            time.sleep(5)


def start():
    threading.Thread(target=poll_forever, name="telegram", daemon=True).start()
