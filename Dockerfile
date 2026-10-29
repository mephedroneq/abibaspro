FROM python:3.12-slim

WORKDIR /usr/src/app

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

# Системные зависимости для Pillow (картинки)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем нужные библиотеки
RUN pip install --no-cache-dir \
    discord.py \
    Pillow

# Копируем код
COPY . .

# Папка для данных (база будет сохраняться)
RUN mkdir -p /app/data && chmod 777 /app/data

# Запускаем бота
CMD ["python", "voice_tracker.py"]
