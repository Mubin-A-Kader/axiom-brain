"""Pytest configuration and shared fixtures.

Test strategy
-------------
- We use a real Postgres (``pgvector/pgvector:pg16``) and Redis via
  Docker Compose, controlled by ``pytest-asyncio`` in ``auto`` mode.
- Each test gets a fresh session in a rolled-back transaction so tests
  are isolated without needing full table teardown.
- The FastAPI ``TestClient`` is replaced with ``httpx.AsyncClient``
  pointing at the real ASGI app.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Point at a test DB — override in CI via env.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://axiom:axiom@localhost:5432/axiom_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from axiom.config import get_settings
from axiom.db.base import Base
from axiom.db.session import get_db_session
from axiom.main import app


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def engine():
    settings = get_settings()
    eng = create_async_engine(str(settings.database_url), echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    """Each test gets an isolated session that rolls back on teardown."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ---------------------------------------------------------------------------
# Override the app dependency so test routes use the fixture session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def bearer(token: str = "test-token") -> dict[str, str]:
    """Produce an ``Authorization`` header for test requests."""
    return {"Authorization": f"Bearer {token}"}
