from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.db import init_db
from app.core.limiter import limiter, rate_limit_handler
from app.core.logging import configure_logging, request_logging_middleware
from app.routers import assets, auth, chat, profiles, sessions, uploads


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = logging.getLogger("vizzy.startup")
    Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)

    if not settings.JWT_SECRET_KEY or len(settings.JWT_SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be set and at least 32 characters long.")

    await init_db()
    logger.info("startup_complete", extra={"event": "startup_complete"})
    yield


app = FastAPI(title="Vizzy AI", version="0.1.0", lifespan=lifespan)
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


@app.get("/")
async def root() -> dict:
    return {"ok": True, "service": "vizzy-api"}


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "image_backend": settings.IMAGE_BACKEND,
        "llm_model": settings.GITHUB_MODEL,
        "storage": "supabase" if settings.SUPABASE_URL else "local",
    }
