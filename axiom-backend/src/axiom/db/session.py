"""Async SQLAlchemy session management.

A single ``AsyncEngine`` is created at startup with a bounded connection
pool. Request handlers get a fresh ``AsyncSession`` via the
``get_db_session`` FastAPI dependency, which commits on success and
rolls back on exception.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from axiom.config import get_settings
from axiom.core.logging import get_logger

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    global _engine, _sessionmaker
    settings = get_settings()
    _engine = create_async_engine(
        str(settings.database_url),
        echo=False,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    log.info("db.connected")


async def close_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        log.info("db.closed")


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("DB not initialized — call init_db() first.")
    return _sessionmaker


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session with transactional semantics."""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
