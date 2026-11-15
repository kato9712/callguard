"""All settings come from environment variables (set them on the Docker container)."""
import os


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


OWNER_NAME = _env("OWNER_NAME", "Ly")

DATA_DIR = _env("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "callguard.db")
SOUNDS_DIR = os.path.join(DATA_DIR, "sounds")        # prompts rendered at startup
REC_DIR = os.path.join(DATA_DIR, "recordings")        # caller intros + voicemails
MODELS_DIR = os.path.join(DATA_DIR, "models")

# FastAGI server that Asterisk connects to
AGI_HOST = _env("AGI_HOST", "127.0.0.1")
AGI_PORT = _int("AGI_PORT", 4573)

# HTTP API used by the Android app (allow/block lists, known-contact hashes)
API_PORT = _int("API_PORT", 8099)
API_TOKEN = _env("API_TOKEN")

# Telegram
TG_TOKEN = _env("TG_TOKEN")
TG_CHAT_ID = _env("TG_CHAT_ID")
TG_API = _env("TG_API", "https://api.telegram.org")   # overridable for tests

# Speech
STT_BACKEND = _env("STT_BACKEND", "auto")             # auto | whisper | sphinx | none
WHISPER_MODEL = _env("WHISPER_MODEL", "base.en")
TTS_BACKEND = _env("TTS_BACKEND", "auto")             # auto | piper | espeak
PIPER_VOICE = _env("PIPER_VOICE", "en_US-lessac-medium")

# Optional LLM (any OpenAI-compatible server, e.g. llama-server on your PC).
# Leave blank to use rules only. If set but unreachable, rules are used.
LLM_URL = _env("LLM_URL")
LLM_MODEL = _env("LLM_MODEL", "local")
LLM_TIMEOUT = _int("LLM_TIMEOUT", 4)

# Call flow timing
INTRO_MAX_SECONDS = _int("INTRO_MAX_SECONDS", 10)
HOLD_SECONDS = _int("HOLD_SECONDS", 45)              # how long the caller waits for your tap
RING_SECONDS = _int("RING_SECONDS", 25)              # how long Connect rings Linphone
VOICEMAIL_MAX_SECONDS = _int("VOICEMAIL_MAX_SECONDS", 120)
SIP_PHONE = _env("SIP_PHONE", "PJSIP/ly")            # your Linphone endpoint

# Test-only: force the "press N" digit so automated tests can pass the challenge.
FIXED_CHALLENGE_DIGIT = _env("FIXED_CHALLENGE_DIGIT")
