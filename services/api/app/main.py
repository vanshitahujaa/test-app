"""URL Shortener API — the realistic AutoFixOps test target.

Layout:
  /metrics              Prometheus scrape target
  /healthz              liveness
  /readyz               readiness (checks Postgres + Redis)
  /shorten              real business: create short link
  /{code}               real business: redirect
  /stats/{code}         real business: click count
  /_chaos/leak          fault injection (CHAOS_ENABLED=true to enable)
  /_chaos/cpu
  /_chaos/crash
  /_chaos/recover
"""

import os
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from .db import init_db, engine
from .cache import cache_client
from .metrics import http_requests_total, http_request_duration_seconds
from .routes import shortener, chaos


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("API ready.")
    yield
    logger.info("API shutting down.")


app = FastAPI(title="shortener-api", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    # Group dynamic paths so we don't blow up label cardinality.
    route = request.url.path
    if route.startswith("/stats/"):
        route = "/stats/{code}"
    elif route.count("/") == 1 and route not in ("/metrics", "/healthz", "/readyz", "/shorten"):
        route = "/{code}"

    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        http_requests_total.labels(route=route, method=request.method, status="500").inc()
        raise
    duration = time.perf_counter() - start
    http_request_duration_seconds.labels(route=route).observe(duration)
    http_requests_total.labels(route=route, method=request.method, status=str(response.status_code)).inc()
    return response


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: Postgres + Redis must both answer."""
    checks = {"postgres": False, "redis": False}
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        checks["postgres"] = True
    except Exception as e:
        logger.warning("readyz: postgres check failed: %s", e)
    try:
        cache_client().ping()
        checks["redis"] = True
    except Exception as e:
        logger.warning("readyz: redis check failed: %s", e)

    healthy = all(checks.values())
    return Response(
        content=str(checks),
        media_type="text/plain",
        status_code=200 if healthy else 503,
    )


app.include_router(shortener.router)
app.include_router(chaos.router, prefix="/_chaos", tags=["chaos"])
