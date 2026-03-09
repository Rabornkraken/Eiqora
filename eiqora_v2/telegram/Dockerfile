# Telegram PDF Broadcast Bot
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-telegram.txt ./
RUN pip install --no-cache-dir -r requirements-telegram.txt

COPY eiqora_v2/telegram/ ./eiqora_v2/telegram/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "eiqora_v2.telegram.bot"]
