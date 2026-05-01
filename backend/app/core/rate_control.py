"""Rate limiting and concurrency control for external API calls.

Provides:
- asyncio.Semaphore singletons for LLM and image backends
- Generic retry with exponential backoff + jitter
- Convenience wrappers that combine semaphore + retry
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger("vizzy.rate_control")

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Semaphores (module-level singletons)
# ---------------------------------------------------------------------------

llm_semaphore = asyncio.Semaphore(5)    # max 5 concurrent LLM calls
image_semaphore = asyncio.Semaphore(2)  # max 2 concurrent image calls

# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------


async def retry_with_backoff(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    operation_name: str = "external_call",
    **kwargs: Any,
) -> T:
    """Execute ``fn(*args, **kwargs)`` with exponential backoff + jitter.

    - delay = base_delay × 2^attempt
    - jitter = ± random(0, delay × 0.3)
    - HTTP 429: respects ``Retry-After`` header when available
    - Logs every retry attempt
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc

            if attempt >= max_retries:
                break

            # Compute backoff
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(-delay * 0.3, delay * 0.3)
            sleep_time = max(0.1, delay + jitter)

            # Special handling for HTTP 429
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_time = max(sleep_time, float(retry_after))
                    except (ValueError, TypeError):
                        pass

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
    """Acquire the image semaphore, add inter-request delay, then retry."""
    async with image_semaphore:
        # Small delay between image requests to avoid 429 spam
        await asyncio.sleep(0.3)
        return await retry_with_backoff(
            fn, *args, **kwargs,
            max_retries=max_retries,
            base_delay=2.0,
            max_delay=45.0,
            operation_name="image_call",
        )
