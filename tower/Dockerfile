FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    TZ=America/Chicago

RUN apt-get update && apt-get install -y --no-install-recommends \
        asterisk asterisk-core-sounds-en asterisk-core-sounds-en-gsm \
        espeak-ng sox ffmpeg python3 python3-pip ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        faster-whisper==1.2.1 piper-tts==1.8.0

COPY asterisk /opt/callguard/asterisk
COPY app /opt/callguard/app
COPY entrypoint.py /opt/callguard/entrypoint.py

VOLUME /data
# Host networking is recommended (SIP + RTP). Ports used: 5060/udp, 10000-10099/udp, 8099/tcp (app API)
ENTRYPOINT ["python3", "/opt/callguard/entrypoint.py"]
