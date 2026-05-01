from __future__ import annotations

from urllib.parse import urlparse
import logging

from app.core.config import settings

logger = logging.getLogger("vizzy.queue")


def redis_settings_from_url():
    from arq.connections import RedisSettings

    parsed = urlparse(settings.REDIS_URL)
    database = parsed.path.lstrip("/")
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(database or 0),
        password=parsed.password,
        username=parsed.username,
        ssl=parsed.scheme == "rediss",
    )


async def enqueue_job(function_name: str, *args) -> bool:
    """Queue a background job.

    Returns False when Redis/arq is unavailable so request handlers can degrade
    gracefully in local development instead of failing the user-facing request.
    """
    try:
        from arq import create_pool
    except Exception:
        return False

    try:
        redis = await create_pool(redis_settings_from_url())
        await redis.enqueue_job(
            function_name,
            *args,
            _defer_by=None,
        )
        await redis.close()
        return True
    except Exception as exc:
        logger.warning("queue_enqueue_failed", extra={"event": "queue_enqueue_failed", "error": str(exc)})
        return False


async def dead_letter_job(function_name: str, args: tuple, error: str) -> None:
    try:
        from arq import create_pool
        import json

        redis = await create_pool(redis_settings_from_url())
        await redis.lpush(
            "vizzy:dead_letter",
            json.dumps({"function": function_name, "args": args, "error": error}, default=str),
        )
        await redis.close()
    except Exception as exc:
        logger.error("dead_letter_write_failed", extra={"event": "dead_letter_write_failed", "error": str(exc)})
