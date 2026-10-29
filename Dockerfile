FROM python:3.12-slim

WORKDIR /usr/src/app

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    discord.py \
    Pillow

COPY . .

RUN mkdir -p /app/data && chmod 777 /app/data

CMD ["python", "voice_tracker.py"]
