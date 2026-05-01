from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.routers import assets, auth, chat, profiles, uploads

from app.core.limiter import limiter, rate_limit_handler
from slowapi.errors import RateLimitExceeded


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keep local dir for fallback mode only
    Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)

    if not settings.JWT_SECRET_KEY or len(settings.JWT_SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be set and at least 32 characters long.")

    await init_db()
    yield


app = FastAPI(title="Vizzy AI", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

# Parse FRONTEND_ORIGIN as comma-separated to support multiple origins
_origins = [o.strip() for o in settings.FRONTEND_ORIGIN.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ❌ REMOVED StaticFiles mount (now using Supabase)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(uploads.router)
app.include_router(profiles.router)
app.include_router(assets.router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "image_backend": settings.IMAGE_BACKEND,
        "llm_model": settings.GITHUB_MODEL,
        "storage": "supabase" if settings.SUPABASE_URL else "local",
    }