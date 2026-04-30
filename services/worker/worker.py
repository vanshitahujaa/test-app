"""Click aggregator.

Reads click events from the Redis stream `clicks` and increments the
per-link counter in Postgres. Emits its own Prometheus metrics on a
sidecar HTTP server.

Failure modes worth knowing about:
  - LEAK_RATE_KB env > 0 simulates a slow memory leak (each batch
    adds garbage to a list). Defaults to 0.
  - BATCH_SIZE controls how many events are aggregated per round-trip.
"""

import os
import time
import logging
import signal
import threading
from collections import Counter

import redis
from sqlalchemy import create_engine, text
from prometheus_client import start_http_server, Counter as PromCounter, Gauge


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] worker: %(message)s")
log = logging.getLogger()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL",
                         "postgresql://shortener:shortener@postgres:5432/shortener")
# Render/Heroku style URL — normalize for SQLAlchemy 2.x
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
STREAM = os.getenv("STREAM", "clicks")
GROUP = os.getenv("GROUP", "click-agg")
CONSUMER = os.getenv("HOSTNAME", "worker-1")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
BLOCK_MS = int(os.getenv("BLOCK_MS", "2000"))
LEAK_RATE_KB = int(os.getenv("LEAK_RATE_KB", "0"))   # set >0 to simulate a leak
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))


# ── metrics ───────────────────────────────────────────────────────
events_processed = PromCounter("worker_events_processed_total",
                               "Click events successfully aggregated.")
events_failed = PromCounter("worker_events_failed_total",
                            "Click events that failed to aggregate.")
batch_size_gauge = Gauge("worker_batch_size", "Last batch size processed.")
db_lag_seconds = Gauge("worker_db_lag_seconds",
                       "Time spent in the most recent DB roundtrip.")


_stop = threading.Event()
_leaked: list[bytearray] = []


def _shutdown(*_):
    log.info("shutdown signal received")
    _stop.set()


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


def _ensure_group(r: redis.Redis):
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        log.info("created consumer group %s", GROUP)
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            log.info("consumer group %s already exists", GROUP)
        else:
            raise


def _aggregate_and_flush(engine, counts: Counter):
    if not counts:
        return
    started = time.perf_counter()
    with engine.begin() as conn:
        for code, n in counts.items():
            conn.execute(
                text("UPDATE links SET clicks = clicks + :n WHERE code = :code"),
                {"n": n, "code": code},
            )
    db_lag_seconds.set(time.perf_counter() - started)


def main():
    log.info("starting worker (batch=%d, leak=%dKB)", BATCH_SIZE, LEAK_RATE_KB)
    start_http_server(METRICS_PORT)
    log.info("metrics on :%d", METRICS_PORT)

    r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
    _ensure_group(r)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=2)

    while not _stop.is_set():
        try:
            entries = r.xreadgroup(
                GROUP, CONSUMER, {STREAM: ">"}, count=BATCH_SIZE, block=BLOCK_MS
            )
        except redis.RedisError as e:
            log.warning("xreadgroup failed: %s", e)
            time.sleep(1)
            continue

        if not entries:
            continue

        ids = []
        counts: Counter[str] = Counter()
        for _stream, batch in entries:
            for entry_id, fields in batch:
                ids.append(entry_id)
                code = fields.get("code")
                if code:
                    counts[code] += 1

        try:
            _aggregate_and_flush(engine, counts)
            r.xack(STREAM, GROUP, *ids)
            events_processed.inc(len(ids))
            batch_size_gauge.set(len(ids))
        except Exception as e:
            log.exception("flush failed: %s", e)
            events_failed.inc(len(ids))

        if LEAK_RATE_KB > 0:
            # opt-in slow leak — exists so AutoFixOps can demonstrate
            # memory-leak detection on a non-api service.
            _leaked.append(bytearray(LEAK_RATE_KB * 1024))

    log.info("worker stopped after processing %d events",
             int(events_processed._value.get()))


if __name__ == "__main__":
    main()
