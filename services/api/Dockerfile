FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.local /root/.local
COPY app ./app
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=2s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/healthz || exit 1
# Shell form so $PORT expands at runtime — Render injects it; locally it defaults to 8000.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
