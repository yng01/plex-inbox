# yt-dlp-telegram-bot
#
# One self-contained image: Python 3.12 + ffmpeg + yt-dlp (pip) + python-telegram-bot.
# The bot talks to Telegram via long polling (outbound only), so no ports are published.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is needed to merge video+audio into multi-audio MKV files
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (layer caching)
RUN pip install \
    "python-telegram-bot==22.8" \
    "yt-dlp>=2025.1.1" \
    "httpx>=0.27"

# Bot code
COPY bot/ ./bot/

# /data holds the library that Plex reads
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /data \
    && chown -R bot:bot /data /app
# We run as the image's root user on purpose: under rootless Docker the
# container's root IS the host user who owns the bind-mounted ./data volume,
# so this keeps the library writable (a uid-1000 user would map to an
# unrelated host uid and get Permission denied on the mount).
USER root

ENV DATA_DIR=/data
VOLUME /data

CMD ["python", "-m", "bot"]
