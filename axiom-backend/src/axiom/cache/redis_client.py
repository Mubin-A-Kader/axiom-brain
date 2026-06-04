"""Async Redis client.

Single shared connection pool. Used for:
- Rate limiting (sliding window).
- LLM response cache (``cache_get`` / ``cache_set``).
- Idempotency keys.
- ARQ background-job queue.
"""

import hashlib
import json
from typing import Any

from redis.asyncio import Redis, from_url

from axiom.config import get_settings
from axiom.core.logging import get_logger

log = get_logger(__name__)

_redis: Redis | None = None


async def init_redis() -> None:
    global _redis
    settings = get_settings()
    _redis = from_url(str(settings.redis_url), decode_responses=True, max_connections=50)
    await _redis.ping()
    log.info("redis.connected")


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        log.info("redis.closed")


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized — call init_redis() first.")
    return _redis


# --- Caching helpers ---------------------------------------------------------


def make_cache_key(namespace: str, payload: Any) -> str:
    """Deterministic SHA256-keyed cache key from arbitrary JSON-serializable payload."""
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"cache:{namespace}:{digest}"


async def cache_get(key: str) -> Any | None:
    raw = await get_redis().get(key)
    return json.loads(raw) if raw else None


async def cache_set(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)
