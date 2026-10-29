FROM python:3.12-slim

WORKDIR /usr/src/app

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    pyTelegramBotAPI \
    python-telegram-bot \
    requests

COPY . .

RUN mkdir -p /app/data && chmod 777 /app/data

RUN echo 'from http.server import HTTPServer, SimpleHTTPRequestHandler\nHTTPServer(("0.0.0.0", 3000), SimpleHTTPRequestHandler).serve_forever()' > /usr/src/app/server.py

CMD ["python", "/usr/src/app/server.py"]
