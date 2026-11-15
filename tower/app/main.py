import logging
import os

from . import agi, api, config, db, flow, speech, telegram


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("callguard")
    for d in (config.DATA_DIR, config.SOUNDS_DIR, config.REC_DIR, config.MODELS_DIR):
        os.makedirs(d, exist_ok=True)
    db.conn()
    tts = speech.render_prompts()
    speech.warm_up()
    log.info("TTS=%s STT=%s LLM=%s Telegram=%s", tts, speech.stt_backend(),
             config.LLM_URL or "off (rules)", "on" if telegram.enabled() else "OFF")
    api.start()
    telegram.start()
    srv = agi.Server((config.AGI_HOST, config.AGI_PORT), flow.handle)
    log.info("FastAGI listening on %s:%s", config.AGI_HOST, config.AGI_PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
