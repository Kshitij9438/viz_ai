from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.db import init_db
from app.core.limiter import limiter, rate_limit_handler

from app.routers import assets, auth, chat, profiles, uploads
from app.routers import sessions


# ---------------------------
# 🔥 LIFESPAN (BOOTSTRAP)
# ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Local fallback directory (only used if Supabase fails)
    Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)

    # 🔐 Critical env validation
    if not settings.JWT_SECRET_KEY or len(settings.JWT_SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be set and >= 32 chars")

    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set")

    # ⚠️ Strong recommendation (not hard fail)
    if not settings.SUPABASE_URL:
        print("⚠️ WARNING: SUPABASE not configured — falling back to local storage")

    await init_db()
    yield


# ---------------------------
# 🚀 APP INIT
# ---------------------------
app = FastAPI(
    title="Vizzy AI",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------
# 🔥 RATE LIMITING (FIXED)
# ---------------------------
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)  # ❗ YOU WERE MISSING THIS


# ---------------------------
# 🌐 CORS (SAFE)
# ---------------------------
_origins = [o.strip() for o in settings.FRONTEND_ORIGIN.split(",") if o.strip()]

if not _origins:
    raise RuntimeError("FRONTEND_ORIGIN must be set (no wildcard allowed in production)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,  # ❗ NO "*" fallback
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------
# 🔗 ROUTERS
# ---------------------------
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(uploads.router)
app.include_router(profiles.router)
app.include_router(assets.router)
app.include_router(sessions.router)


# ---------------------------
# ❤️ HEALTH CHECK
# ---------------------------
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "vizzy-ai",
        "version": "1.0.0",
        "environment": "production",
        "image_backend": settings.IMAGE_BACKEND,
        "llm_model": settings.GITHUB_MODEL,
        "storage": "supabase" if settings.SUPABASE_URL else "local",
        "cors_origins": _origins,
    }