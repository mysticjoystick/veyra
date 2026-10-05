# Veyra dashboard — production image (Telegram held: browser + PWA alerts only).
# Long-running uvicorn (WebSocket + background runner need a persistent
# process, which is why Render/Railway/Fly/VPS fit and Vercel does not).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VEYRA_ENVIRONMENT=prod \
    PORT=8000

WORKDIR /app

# Deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# App code (data/ stays on a persistent volume, never baked into the image).
COPY src/ ./src/
COPY config/.env.sample ./config/.env.sample

# Writable dirs for SQLite / Parquet / alert caches (mount a disk over /app/data).
RUN mkdir -p /app/data/candles /app/data/alerts /app/data/paper_live /app/data/postback /app/data/logs

EXPOSE 8000

# $PORT is injected by the host (Render/Railway/Fly). Shell form expands it.
# GZip + shell-first paint keep mobile 4G under the 2s budget.
CMD sh -c "uvicorn veyra.web:dashboard_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --workers 1"
