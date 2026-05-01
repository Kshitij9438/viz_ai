"""Rate limiting and concurrency control for external API calls.

Provides:
- asyncio.Semaphore singletons for LLM and image backends
- Generic retry with exponential backoff + jitter + 429 awareness
- Adaptive delay that increases under sustained rate limiting
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger("vizzy.rate_control")

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Semaphores (module-level singletons)
# ---------------------------------------------------------------------------

llm_semaphore = asyncio.Semaphore(5)    # max 5 concurrent LLM calls
image_semaphore = asyncio.Semaphore(1)  # max 1 concurrent image call (Pollinations can't handle burst)

# ---------------------------------------------------------------------------
# Adaptive delay tracker for image API
# ---------------------------------------------------------------------------

_consecutive_429s: int = 0
_image_base_delay: float = 0.8  # seconds between image requests
_DELAY_NORMAL: float = 0.8
_DELAY_THROTTLED: float = 1.5
_THROTTLE_THRESHOLD: int = 2  # after 2 consecutive 429s, increase delay


def _get_image_delay() -> float:
    """Return current inter-request delay, increased under throttling."""
    if _consecutive_429s >= _THROTTLE_THRESHOLD:
        return _DELAY_THROTTLED
    return _DELAY_NORMAL


def _record_429() -> None:
    global _consecutive_429s
    _consecutive_429s += 1
    if _consecutive_429s == _THROTTLE_THRESHOLD:
        logger.warning(
            "adaptive_throttle_engaged",
            extra={
                "event": "adaptive_throttle_engaged",
                "consecutive_429s": _consecutive_429s,
                "new_delay": _DELAY_THROTTLED,
            },
        )


def _record_success() -> None:
    global _consecutive_429s
    if _consecutive_429s > 0:
        logger.info(
            "adaptive_throttle_released",
            extra={"event": "adaptive_throttle_released"},
        )
    _consecutive_429s = 0


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------


async def retry_with_backoff(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    operation_name: str = "external_call",
    **kwargs: Any,
) -> T:
    """Execute ``fn(*args, **kwargs)`` with exponential backoff + jitter.

    - delay = base_delay × 2^attempt
    - jitter = random(0, delay × 0.3)
    - HTTP 429: uses Retry-After header or a generous fixed delay
    - Logs every retry attempt with structured fields
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await fn(*args, **kwargs)
            if operation_name == "image_call":
                _record_success()
            return result
        except Exception as exc:
            last_exc = exc

            if attempt >= max_retries:
                break

            # Compute backoff
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.3)
            sleep_time = delay + jitter

            # Special 429 handling — generous delay
            is_429 = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            if is_429:
                if operation_name == "image_call":
                    _record_429()

                retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_time = max(sleep_time, float(retry_after) + random.uniform(0.5, 1.5))
                    except (ValueError, TypeError):
                        pass
                else:
                    # No Retry-After header — use generous delay
                    sleep_time = max(sleep_time, 2.0 + random.uniform(0.5, 2.0))

                logger.warning(
                    "image_request_429",
                    extra={
                        "event": "image_request_429",
                        "attempt": attempt + 1,
                        "delay_seconds": round(sleep_time, 2),
                        "consecutive_429s": _consecutive_429s,
                    },
                )

            logger.warning(
                "retry_attempt",
                extra={
                    "event": "retry_attempt",
                    "target": operation_name,
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "delay_seconds": round(sleep_time, 2),
                    "reason": str(exc)[:200],
                },
            )

            await asyncio.sleep(sleep_time)

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Rate-limited wrappers
# ---------------------------------------------------------------------------


async def rate_limited_llm_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    **kwargs: Any,
) -> T:
    """Acquire the LLM semaphore, then execute with retry + backoff."""
    async with llm_semaphore:
        return await retry_with_backoff(
            fn, *args, **kwargs,
            max_retries=max_retries,
            base_delay=1.0,
            operation_name="llm_call",
        )


async def rate_limited_image_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    **kwargs: Any,
) -> T:
    """Acquire the image semaphore, add adaptive inter-request delay, then retry."""
    async with image_semaphore:
        # Adaptive delay — increases under sustained 429s
        delay = _get_image_delay()
        logger.debug(
            "image_request_started",
            extra={"event": "image_request_started", "delay": delay},
        )
        await asyncio.sleep(delay)
        return await retry_with_backoff(
            fn, *args, **kwargs,
            max_retries=max_retries,
            base_delay=2.5,
            max_delay=45.0,
            operation_name="image_call",
        )
