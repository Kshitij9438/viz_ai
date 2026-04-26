from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.db import init_db
from app.routers import assets, chat, profiles, uploads


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="Vizzy AI", version="0.1.0", lifespan=lifespan)

# Parse FRONTEND_ORIGIN as comma-separated to support multiple origins
_origins = [o.strip() for o in settings.FRONTEND_ORIGIN.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory=settings.STORAGE_DIR), name="storage")

app.include_router(chat.router)
app.include_router(uploads.router)
app.include_router(profiles.router)
app.include_router(assets.router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "image_backend": settings.IMAGE_BACKEND, "llm_model": settings.GITHUB_MODEL}
