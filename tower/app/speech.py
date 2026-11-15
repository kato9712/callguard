"""Text-to-speech for prompts, speech-to-text for caller audio, and a simple speech detector."""
import array
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import wave

from . import config

log = logging.getLogger("speech")

# ---------------------------------------------------------------- prompts

def prompts():
    o = config.OWNER_NAME
    p = {
        "greet": f"Hi, you've reached {o}'s phone. This call is being screened. "
                 "After the tone, please say your name and why you're calling.",
        "greet_again": "Sorry, I didn't catch that. After the tone, please say your name and why you're calling.",
        "hold": f"Thanks. Please hold while I see if {o} is available.",
        "still_holding": "Still trying. Please hold.",
        "connecting": "Connecting you now.",
        "vm_screen": f"{o} isn't available right now. Please leave a message after the tone, "
                     "and press pound when you're done.",
        "vm_known": f"Hi, you've reached {o}. I can't answer right now. "
                    "Leave a message after the tone, and press pound when you're done.",
        "msg_sent": "Thanks, your message was sent. Goodbye.",
        "goodbye": "Goodbye.",
        "blocked": "This number can't be connected. Goodbye.",
    }
    for d in range(1, 10):
        p[f"press_{d}"] = f"Thanks. To be connected, press {d} now."
        p[f"press_retry_{d}"] = f"Please press {d} on your keypad to continue."
    return p


def _to_asterisk_wav(src, dst):
    """Asterisk plays .wav as 8 kHz, mono, 16-bit signed PCM."""
    subprocess.run(["sox", src, "-r", "8000", "-c", "1", "-b", "16", "-e", "signed-integer", dst],
                   check=True, capture_output=True)


def _piper_voice_path():
    return os.path.join(config.MODELS_DIR, f"{config.PIPER_VOICE}.onnx")


def _ensure_piper_voice():
    path = _piper_voice_path()
    if os.path.exists(path):
        return path
    os.makedirs(config.MODELS_DIR, exist_ok=True)
    try:
        subprocess.run(["python3", "-m", "piper.download_voices", config.PIPER_VOICE,
                        "--data-dir", config.MODELS_DIR], check=True, capture_output=True, timeout=300)
    except Exception as e:  # no internet, piper missing, etc.
        log.warning("Piper voice download failed (%s); falling back to espeak-ng", e)
        return None
    return path if os.path.exists(path) else None


def _tts_backend():
    if config.TTS_BACKEND in ("piper", "auto"):
        try:
            import piper  # noqa: F401
            if _ensure_piper_voice():
                return "piper"
        except ImportError:
            pass
        if config.TTS_BACKEND == "piper":
            log.warning("Piper requested but unavailable; using espeak-ng")
    return "espeak"


def render_prompts(force=False):
    """Render every prompt once into SOUNDS_DIR. Re-renders when OWNER_NAME or the voice changes."""
    os.makedirs(config.SOUNDS_DIR, exist_ok=True)
    backend = _tts_backend()
    stamp_path = os.path.join(config.SOUNDS_DIR, ".stamp")
    stamp = f"{backend}|{config.PIPER_VOICE}|{config.OWNER_NAME}|v2"
    if not force and os.path.exists(stamp_path) and open(stamp_path).read() == stamp:
        return backend
    voice = None
    if backend == "piper":
        from piper import PiperVoice
        voice = PiperVoice.load(_piper_voice_path())
    for name, text in prompts().items():
        dst = os.path.join(config.SOUNDS_DIR, f"{name}.wav")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            raw = tmp.name
        try:
            if voice is not None:
                with wave.open(raw, "wb") as wf:
                    voice.synthesize_wav(text, wf)
            else:
                subprocess.run(["espeak-ng", "-v", "en-us", "-s", "155", "-w", raw, text],
                               check=True, capture_output=True)
            _to_asterisk_wav(raw, dst)
        finally:
            os.unlink(raw)
    with open(stamp_path, "w") as f:
        f.write(stamp)
    log.info("Rendered %d prompts with %s", len(prompts()), backend)
    return backend


def sound(name):
    """Path for Asterisk STREAM FILE (no extension)."""
    return os.path.join(config.SOUNDS_DIR, name)


# ---------------------------------------------------------------- speech detection

def speech_seconds(path):
    """Seconds of audio that sound like a voice (energy well above the noise floor).
    Silence, line hiss and a dead line all come out near 0."""
    try:
        with wave.open(path, "rb") as wf:
            if wf.getsampwidth() != 2:
                return 0.0
            rate = wf.getframerate()
            ch = wf.getnchannels()
            samples = array.array("h", wf.readframes(wf.getnframes()))
    except (FileNotFoundError, wave.Error, EOFError):
        return 0.0
    if ch > 1:
        samples = samples[::ch]
    frame = max(1, rate // 50)  # 20 ms
    rms = []
    for i in range(0, len(samples) - frame + 1, frame):
        chunk = samples[i:i + frame]
        rms.append((sum(s * s for s in chunk) / frame) ** 0.5)
    if not rms:
        return 0.0
    floor = sorted(rms)[len(rms) // 10]           # 10th percentile ~ background noise
    threshold = max(400.0, floor * 4)
    voiced = sum(1 for r in rms if r > threshold)
    return voiced * 0.02


# ---------------------------------------------------------------- speech-to-text

_stt_lock = threading.Lock()
_whisper = None
_whisper_broken = False


def _whisper_model():
    global _whisper, _whisper_broken
    if _whisper_broken:
        raise RuntimeError("whisper model unavailable")
    if _whisper is None:
        from faster_whisper import WhisperModel
        try:
            _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                    download_root=config.MODELS_DIR, cpu_threads=4)
        except Exception:
            _whisper_broken = True   # e.g. no internet on first start; restart the container to retry
            raise
    return _whisper


def _transcribe_whisper(path):
    model = _whisper_model()
    segments, _ = model.transcribe(path, language="en", beam_size=1, vad_filter=False,
                                   condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def _transcribe_sphinx(path):
    from pocketsphinx import Decoder
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw = tmp.name
    try:
        subprocess.run(["sox", path, "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer",
                        "-t", "raw", raw], check=True, capture_output=True)
        data = open(raw, "rb").read()
    finally:
        os.unlink(raw)
    dec = Decoder(loglevel="FATAL")
    dec.start_utt()
    dec.process_raw(data, full_utt=True)
    dec.end_utt()
    hyp = dec.hyp()
    return hyp.hypstr if hyp else ""


def stt_backend():
    b = config.STT_BACKEND
    if b == "none":
        return "none"
    if b in ("whisper", "auto") and not _whisper_broken:
        try:
            import faster_whisper  # noqa: F401
            return "whisper"
        except ImportError:
            if b == "whisper":
                log.warning("faster-whisper not installed")
    if b in ("sphinx", "auto"):
        try:
            import pocketsphinx  # noqa: F401
            return "sphinx"
        except ImportError:
            pass
    return "none"


def transcribe(path):
    """Best-effort transcript; returns '' on any failure (the call flow never depends on it)."""
    if not path or not os.path.exists(path) or speech_seconds(path) < 0.3:
        return ""
    backend = stt_backend()
    try:
        with _stt_lock:
            if backend == "whisper":
                try:
                    return _transcribe_whisper(path)
                except Exception as e:
                    log.warning("Whisper failed (%s); trying pocketsphinx", e)
                    backend = "sphinx"
            if backend == "sphinx":
                return _transcribe_sphinx(path)
    except Exception as e:
        log.warning("Transcription failed: %s", e)
    return ""


def warm_up():
    """Load the STT model at startup so the first call isn't slow."""
    if stt_backend() == "whisper":
        try:
            _whisper_model()
            log.info("Whisper model %s loaded", config.WHISPER_MODEL)
        except Exception as e:
            log.warning("Whisper model load failed (%s); pocketsphinx will be used", e)


def to_ogg(path):
    """Telegram voice notes must be OGG/Opus."""
    out = os.path.splitext(path)[0] + ".ogg"
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c:a", "libopus",
                        "-b:a", "24k", out], check=True, capture_output=True)
        return out
    return None
