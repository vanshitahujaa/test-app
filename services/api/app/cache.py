"""Redis connection — cache lookups and the click event stream."""

import os
import logging
import redis

logger = logging.getLogger("api.cache")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CLICK_STREAM = "clicks"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)


def cache_client() -> redis.Redis:
    return _client


def cache_get(code: str) -> str | None:
    try:
        return _client.get(f"link:{code}")
    except redis.RedisError as e:
        logger.warning("cache_get failed: %s", e)
        return None


def cache_set(code: str, target_url: str) -> None:
    try:
        _client.set(f"link:{code}", target_url, ex=CACHE_TTL_SECONDS)
    except redis.RedisError as e:
        logger.warning("cache_set failed: %s", e)


def emit_click(code: str) -> None:
    """Publish a click event to the Redis stream (worker consumes)."""
    try:
        _client.xadd(CLICK_STREAM, {"code": code}, maxlen=10000, approximate=True)
    except redis.RedisError as e:
        logger.warning("emit_click failed: %s", e)
