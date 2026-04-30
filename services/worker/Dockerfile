FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH
COPY --from=builder /root/.local /root/.local
COPY worker.py ./worker.py
EXPOSE 9100
CMD ["python", "-u", "worker.py"]
