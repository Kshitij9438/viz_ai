from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.db import init_db
from app.core.limiter import limiter, rate_limit_handler
from app.core.logging import configure_logging, request_logging_middleware
from app.core.queue import close_redis, get_redis, redis_health
from app.routers import assets, auth, chat, jobs, profiles, sessions, uploads
from app.worker import JobWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = logging.getLogger("vizzy.startup")
    Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)

    if not settings.JWT_SECRET_KEY or len(settings.JWT_SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be set and at least 32 characters long.")

    await init_db()

    # ---- Redis (non-fatal) ----
    redis = await get_redis()
    if redis:
        logger.info("redis_connected", extra={"event": "redis_connected"})
    else:
        logger.warning(
            "redis_unavailable_at_startup",
            extra={"event": "redis_unavailable", "operation": "startup"},
        )

    # ---- Worker ----
    worker = JobWorker()
    await worker.start()

    # ---- Watchdog: restart worker if it dies, with limits ----
    restart_times: list[float] = []
    watchdog_running = True

    async def _watchdog():
        nonlocal watchdog_running
        while watchdog_running:
            await asyncio.sleep(10)
            if not watchdog_running:
                break
            if worker._task and worker._task.done():
                # Log the exception that killed the worker
                try:
                    exc = worker._task.exception()
                    if exc:
                        logger.error(
                            "worker_died",
                            extra={"event": "worker_died", "error": str(exc)},
                        )
                except asyncio.CancelledError:
                    pass

                # Check restart budget
                now = time.time()
                restart_times.append(now)
                # Prune entries outside the window
                recent = [t for t in restart_times if now - t < settings.WORKER_RESTART_WINDOW]
                restart_times.clear()
                restart_times.extend(recent)

                if len(recent) >= settings.WORKER_MAX_RESTARTS:
                    logger.critical(
                        "worker_restart_limit_exceeded",
                        extra={
                            "event": "worker_restart_limit_exceeded",
                            "restarts_in_window": len(recent),
                            "window_seconds": settings.WORKER_RESTART_WINDOW,
                        },
                    )
                    break  # stop restarting — something is fundamentally broken

                logger.warning(
                    "worker_restarting",
                    extra={
                        "event": "worker_restarting",
                        "restart_count": len(recent),
                    },
                )
                await worker.start()

    watchdog_task = asyncio.create_task(_watchdog(), name="vizzy-watchdog")

    logger.info("startup_complete", extra={"event": "startup_complete"})
    yield

    # ---- Shutdown ----
    watchdog_running = False
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

    await worker.stop()
    await close_redis()


app = FastAPI(title="Vizzy AI", version="0.2.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.middleware("http")(request_logging_middleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        },
    )


_origins = [origin.strip() for origin in settings.FRONTEND_ORIGIN.split(",") if origin.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(uploads.router)
app.include_router(profiles.router)
app.include_router(assets.router)
app.include_router(jobs.router)


@app.get("/")
async def root() -> dict:
    return {"ok": True, "service": "vizzy-api"}


@app.get("/health")
async def health() -> dict:
    redis_ok = await redis_health()
    return {
        "ok": True,
        "image_backend": settings.IMAGE_BACKEND,
        "llm_model": settings.GITHUB_MODEL,
        "storage": "supabase" if settings.SUPABASE_URL else "local",
        "redis": "connected" if redis_ok else "unavailable",
    }
