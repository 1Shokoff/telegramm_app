FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Moscow

WORKDIR /app

RUN adduser --disabled-password --gecos "" --uid 10001 app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY webapp ./webapp

RUN mkdir -p /data && chown -R app:app /data /app

USER app
EXPOSE 8080

CMD ["python", "-m", "app.main"]
