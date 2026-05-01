"""Redis-backed job queue — transport only.

Redis is used EXCLUSIVELY as a message transport (LPUSH / BRPOP).
PostgreSQL is the single source of truth for all job state.

All Redis operations are wrapped in try/except — they NEVER crash the app.
"""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("vizzy.queue")

QUEUE_KEY = "vizzy:jobs"
DEDUP_PREFIX = "vizzy:dedup:"

# ---------------------------------------------------------------------------
# Singleton Redis client
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None
_redis_init_attempted: bool = False


async def get_redis() -> aioredis.Redis | None:
    """Return the singleton Redis client, lazily initialised.

    Returns ``None`` if the connection cannot be established.  The caller
    MUST handle the ``None`` case — Redis being unavailable is a *degraded*
    state, never a crash.
    """
    global _redis, _redis_init_attempted

    if _redis is not None:
        return _redis

    if _redis_init_attempted:
        # Already tried and failed — don't retry on every request.
        # The worker reconnect loop will reset this flag.
        return None

    _redis_init_attempted = True

    try:
        parsed = urlparse(settings.REDIS_URL)
        use_ssl = parsed.scheme == "rediss"

        _redis = aioredis.Redis(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            db=int((parsed.path or "/0").lstrip("/") or 0),
            username=parsed.username,
            password=parsed.password,
            ssl=use_ssl,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
            max_connections=10,
            decode_responses=True,
        )
        # Verify connectivity
        await _redis.ping()
        logger.info("redis_connected", extra={"event": "redis_connected"})
        return _redis

    except Exception as exc:
        logger.warning(
            "redis_connect_failed",
            extra={"event": "redis_unavailable", "error": str(exc)},
        )
        _redis = None
        return None


async def reset_redis_state() -> None:
    """Allow the worker reconnect loop to retry after a failure."""
    global _redis, _redis_init_attempted
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
    _redis = None
    _redis_init_attempted = False


async def close_redis() -> None:
    """Called on application shutdown."""
    global _redis, _redis_init_attempted
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
    _redis = None
    _redis_init_attempted = False


# ---------------------------------------------------------------------------
# Queue operations (transport only)
# ---------------------------------------------------------------------------

async def enqueue_job(job_id: str) -> bool:
    """Push a job_id onto the queue.

    Returns ``False`` when Redis is unavailable or queue depth exceeds
    the configured maximum (backpressure).
    """
    r = await get_redis()
    if r is None:
        logger.warning(
            "enqueue_skipped_no_redis",
            extra={"event": "redis_unavailable", "operation": "enqueue", "job_id": job_id},
        )
        return False

    try:
        # Backpressure check
        depth = await r.llen(QUEUE_KEY)
        if depth >= settings.QUEUE_MAX_DEPTH:
            logger.warning(
                "backpressure_applied",
                extra={
                    "event": "backpressure_applied",
                    "queue_depth": depth,
                    "max_depth": settings.QUEUE_MAX_DEPTH,
                },
            )
            return False

        await r.lpush(QUEUE_KEY, job_id)
        logger.info(
            "job_enqueued_to_redis",
            extra={"event": "job_enqueued", "job_id": job_id, "queue_depth": depth + 1},
        )
        return True

    except Exception as exc:
        logger.warning(
            "enqueue_failed",
            extra={"event": "redis_unavailable", "operation": "enqueue", "error": str(exc)},
        )
        return False


async def dequeue_job(timeout: int = 5) -> str | None:
    """Non-blocking pop from the queue.  Returns a job_id or ``None``.

    Uses RPOP instead of BRPOP for Upstash compatibility — Upstash is
    serverless Redis and does not support long-lived blocking commands.
    The worker loop handles polling via asyncio.sleep.

    Raises ``ConnectionError`` on Redis disconnect so the worker loop can
    handle reconnection.
    """
    r = await get_redis()
    if r is None:
        raise ConnectionError("Redis unavailable for dequeue")

    result = await r.rpop(QUEUE_KEY)
    return result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _dedup_key(user_id: str, session_id: str, message: str) -> str:
    """Deterministic dedup key from normalised inputs."""
    normalised = f"{user_id}:{session_id}:{message.strip().lower()}"
    digest = hashlib.sha256(normalised.encode()).hexdigest()[:16]
    return f"{DEDUP_PREFIX}{digest}"


async def check_dedup(user_id: str, session_id: str, message: str) -> str | None:
    """Check if this request is a duplicate within the TTL window.

    Returns the existing ``job_id`` if duplicate, ``None`` if new.
    """
    r = await get_redis()
    if r is None:
        return None  # can't dedup without Redis — allow the request

    key = _dedup_key(user_id, session_id, message)
    try:
        existing = await r.get(key)
        return existing  # None if new, job_id string if duplicate
    except Exception:
        return None


async def set_dedup(user_id: str, session_id: str, message: str, job_id: str) -> None:
    """Mark this request as in-flight for deduplication (30 s TTL)."""
    r = await get_redis()
    if r is None:
        return

    key = _dedup_key(user_id, session_id, message)
    try:
        await r.set(key, job_id, ex=30)
    except Exception:
        pass  # dedup is best-effort


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def redis_health() -> bool:
    """Return ``True`` if Redis is reachable."""
    r = await get_redis()
    if r is None:
        return False
    try:
        return await r.ping()
    except Exception:
        return False
