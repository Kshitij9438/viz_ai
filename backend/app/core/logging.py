from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import Request, Response


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter with full event taxonomy."""

    _EXTRA_KEYS = (
        # Request lifecycle
        "request_id", "method", "path", "status_code", "duration_ms",
        "event", "error",
        # Job lifecycle
        "job_id", "attempt", "will_retry", "type", "source",
        "original_status", "re_enqueued",
        "status", "status_before", "status_after", "has_result",
        # Queue & backpressure
        "queue_depth", "max_depth", "operation",
        # Rate control
        "target", "max_retries", "delay_seconds", "reason", "consecutive_429s",
        # Worker
        "consecutive_failures", "backoff_seconds",
        "restart_count", "restarts_in_window", "window_seconds",
        "worker_replica",
        # Dedup
        "user_id", "count",
        # Chat / design context (never use LogRecord-reserved keys in extra)
        "session_id", "intent", "user_message", "pipeline_intent",
        "design_ready", "soft_escalate",
        "subject", "style", "colors", "mood",
        "ready", "has_subject", "has_style", "has_colors", "has_mood", "has_visual_cue",
        # Prompt audit
        "prompt", "prompt_length", "prompt_len",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        for key in self._EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(JsonFormatter())
        root.setLevel(logging.INFO)
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def request_logging_middleware(request: Request, call_next) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    logger = logging.getLogger("vizzy.request")

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.exception(
            "request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
                "event": "request_failed",
                "error": str(exc),
            },
        )
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "event": "request_completed",
        },
    )
    return response
