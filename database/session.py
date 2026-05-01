"""Database session management and connection pooling for NITS Arena."""

from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database.models import Base
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Engine & session factory (module-level singletons)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_database_url() -> str:
    """Construct the asyncpg database URL from environment variables.

    Expected env vars (set in ``.env``)::

        DATABASE_URL=postgresql+asyncpg://user:password@host:port/dbname

    Falls back to constructing the URL from individual components if
    ``DATABASE_URL`` is not set.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Ensure the driver is asyncpg
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    user = os.getenv("POSTGRES_USER", "nitsarena")
    password = os.getenv("POSTGRES_PASSWORD", "changeme")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "nitsarena")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def init_db(database_url: str | None = None) -> None:
    """Initialise the engine and session factory.

    Call once at bot startup (in ``bot.py``) before any database usage.

    Args:
        database_url: Optional override for the database URL.  When omitted,
            the URL is inferred from environment variables.
    """
    global _engine, _async_session_factory  # noqa: PLW0603

    url = database_url or _build_database_url()
    logger.info("Initialising database engine: %s", url.split("@")[-1])  # hide credentials

    _engine = create_async_engine(
        url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    _async_session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    logger.info("Database engine ready.")


async def create_tables() -> None:
    """Create all tables defined in the ORM models (DDL).

    Intended for development/testing. Production deployments should use
    Alembic migrations instead.
    """
    global _engine  # noqa: PLW0603
    assert _engine is not None, "Call init_db() before create_tables()."
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (or already exist).")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async generator that yields a database session.

    Intended for use as a dependency or context manager::

        async with get_session() as session:
            ...

    Yields:
        An :class:`AsyncSession` that is committed on clean exit and rolled
        back on exception.
    """
    assert _async_session_factory is not None, "Call init_db() first."
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Dispose the engine and release all pooled connections."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed.")
        _engine = None
