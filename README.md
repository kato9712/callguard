# CallGuard

A free AI call screener that runs on Tower, plus a small Android app. It works like Cloaked Call Guard, but nothing to pay.

```
Unknown caller ─► your phone (CallGuard app rejects it before it rings)
              ─► carrier "forward when busy" ─► free Callcentric NY number
              ─► Tower (Asterisk + screener):
                   "Say your name and why you're calling" → "Press 7"
                   ├─ no answer / recording / wrong key → hang up (you never hear about it)
                   └─ real person → Telegram: transcript + [Connect] [Take message] [Block] [Always allow]
                        Connect → rings Linphone on your phone (over Tailscale)
                        Take message / no tap in 45 s → voicemail audio + transcript in Telegram
```

Contacts, numbers you've allowed, numbers you called in the last 7 days, and anyone who calls twice within 5 minutes ring through normally. Missed calls from contacts also land on Tower as plain voicemail (no screening), sent to Telegram with a transcript.

Your desktop doesn't need to be on. Speech-to-text (Whisper `base.en`) and the voice (Piper) run on Tower's CPU. The spam/legit hint comes from keyword rules. If you set `LLM_URL`, it uses your Qwen server whenever the PC happens to be on, and falls back to the rules when it's off.

**Cost: $0.** Callcentric's free NY number has free unlimited incoming calls. Forwarded calls use your normal plan minutes.

---

## 1. Callcentric (free number)

1. Sign up at callcentric.com (free account). You get an account number like `1777xxxxxxx`.
2. Order the **Free Phone Number** plan (New York numbers, free incoming calls). Write down the new number.
3. In the Callcentric portal, set the SIP password for your account or extension. `CC_USER` = the 1777 number, `CC_PASS` = that password.

## 2. Telegram bot

Make a **new** bot with @BotFather. Don't reuse one another service already reads updates from, because only one program can poll a bot. Send the bot any message, then get your chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## 3. Run it on Tower

```bash
docker run -d --name callguard --network host --restart unless-stopped \
  -v /mnt/user/appdata/callguard:/data \
  -e OWNER_NAME=Ly \
  -e CC_USER=1777xxxxxxx -e CC_PASS='callcentric-sip-password' \
  -e PHONE_PASS='long-random-password-for-linphone' \
  -e EXTERNAL_HOST=qply.us \
  -e TG_TOKEN='123:abc' -e TG_CHAT_ID='123456789' \
  -e API_TOKEN='long-random-token-for-the-app' \
  ghcr.io/kato9712/callguard:latest
```

* The image is private: run `docker login ghcr.io -u kato9712` once with a GitHub token that has `read:packages`. Or build it yourself: `docker build -t callguard tower/`.
* **Router:** forward **UDP 10000–10099** to Tower (call audio). **Don't** forward 5060. Callcentric reaches Tower through its own registration, and Linphone uses Tailscale.
* Ports used on Tower: 5060/udp (SIP), 10000–10099/udp (audio), 8099/tcp (app API, LAN/Tailscale only), 4573 (local only).
* First start downloads the Whisper model and Piper voice (~200 MB) into `/data/models`. Uses ~400 MB RAM.
* Check it: `docker logs callguard` should show `TTS=piper STT=whisper`, and
  `docker exec callguard asterisk -rx "pjsip show registrations"` should say **Registered**.

Optional settings: `HOLD_SECONDS` (45), `RING_SECONDS` (25), `VOICEMAIL_MAX_SECONDS` (120), `WHISPER_MODEL` (`base.en`; `small.en` = more accurate but slower), `PIPER_VOICE`, `LLM_URL=http://192.168.4.23:8080`.

## 4. Linphone (for Connect)

Install Linphone (free) and turn on Tailscale on the phone. Add a SIP account:
username `ly`, password = `PHONE_PASS`, domain = Tower's Tailscale IP, transport UDP.
In Linphone settings, turn on **Keep alive service** so Android doesn't put it to sleep. Dial **600** for an echo test.
If Linphone isn't connected, the Telegram alert simply hides the Connect button.

## 5. Android app

Install `CallGuard.apk` from the repo's **latest** release (Android 10+). In the app:

1. **Set as call screener**, **Allow** contacts (and the call log if you want numbers you dialed to ring).
2. Tower address `http://<Tower Tailscale IP>:8099`, the `API_TOKEN`, your cell number → **Save & sync**.
3. Under *Forward to the screener*, enter the Callcentric number and tap your carrier. Your phone app opens with the code filled in (`**004*1NUMBER#` on T-Mobile/AT&T, `*71NUMBER` on Verizon). Press call.

Test: call your cell from a number that isn't in your contacts. It shouldn't ring, and the caller should hear the screener.

## Telegram commands

`/recent` · `/allow 4055550100` · `/block 4055550100` · `/unblock 4055550100` · `/lists`

## Tests

`tower/tests/e2e.py` runs real Asterisk and places 18 simulated calls through it: robocalls, dead air, wrong keys, hang-ups, connect, voicemail, timeouts, blocks, allows, contacts, hidden numbers, the API and the Telegram commands. CI runs it with Whisper and Piper on every push, plus the SIP config check, classifier tests, and Android unit tests.
