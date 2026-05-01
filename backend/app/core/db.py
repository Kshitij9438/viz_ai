from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
import ssl

from app.core.config import settings


class Base(DeclarativeBase):
    pass


# 🔥 SSL setup (required for Supabase)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


# 🔥 CRITICAL: disable prepared statements for PgBouncer compatibility
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args={
        "ssl": ssl_context,
        "statement_cache_size": 0,  # 🚨 FIXES YOUR ERROR
    },
)


AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import models so they register on Base.metadata
    from app.models import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)