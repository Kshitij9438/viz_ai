from __future__ import annotations

import ssl
import logging

from sqlalchemy import text
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _build_ssl_context(database_url: str):
    """
    Build SSL context for asyncpg.

    - Required for Railway / Supabase (TLS enforced)
    - Disables strict verification to avoid self-signed cert failure
    """
    if database_url.startswith("postgresql"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _connect_args(database_url: str) -> dict:
    """
    Driver-specific connection args for asyncpg.
    """
    if database_url.startswith("postgresql"):
        return {
            "ssl": _build_ssl_context(database_url),
            "statement_cache_size": 0,  # safer for serverless / pooled infra
        }
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


# ✅ Engine (production-tuned)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,

    # 🔥 IMPORTANT for Railway-like environments
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,

    connect_args=_connect_args(settings.DATABASE_URL),
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):  # type: ignore[unused-argument]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


# ✅ Session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ✅ Dependency
async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ✅ Safe DB init (DO NOT crash app)
async def init_db() -> None:
    from app.models import models  # noqa: F401

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if settings.DATABASE_URL.startswith("postgresql"):
                await conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress JSONB"))
            elif settings.DATABASE_URL.startswith("sqlite"):
                try:
                    await conn.execute(text("ALTER TABLE jobs ADD COLUMN progress JSON"))
                except Exception:
                    pass
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)
        # DO NOT raise → app should still start
