"""FastAPI application factory and entrypoint.

The app is wired together here using a lifespan context manager so that
connection pools (Postgres, Redis) are created on startup and gracefully
closed on shutdown. Middleware ordering matters: request-ID first so
every downstream log line and trace can be correlated.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from axiom.api.middleware import RequestIDMiddleware, TimingMiddleware
from axiom.api.v1.router import api_router
from axiom.cache.redis_client import close_redis, init_redis
from axiom.config import get_settings
from axiom.core.exceptions import register_exception_handlers
from axiom.core.logging import configure_logging, get_logger
from axiom.core.metrics import setup_metrics
from axiom.core.tracing import setup_tracing
from axiom.db.session import close_db, init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown hooks for resource management."""
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("startup.begin", env=settings.app_env, service=settings.otel_service_name)

    await init_db()
    await init_redis()
    setup_tracing(app)

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        await close_redis()
        await close_db()
        log.info("shutdown.complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AXIOM",
        description="Agent orchestration with RAG, evals, and observability.",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    # Middleware (outermost first)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    setup_metrics(app)

    app.include_router(api_router, prefix="/v1")

    return app


app = create_app()
