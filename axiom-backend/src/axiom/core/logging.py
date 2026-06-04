"""Structured logging.

We use structlog so every log line is a key-value record. In production
the renderer emits JSON for ingestion by Loki / Datadog / ES; in
development we render colorized key-value lines.

Every request handler should get a logger via ``get_logger(__name__)``
and attach a ``request_id`` (auto-bound by middleware) so log lines
can be correlated with traces.
"""

import logging
import sys

import structlog
from structlog.contextvars import merge_contextvars
from structlog.types import Processor


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging.

    Idempotent — safe to call multiple times in tests.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    # In prod -> JSON; in dev -> human-readable
    is_tty = sys.stderr.isatty()
    renderer: Processor = (
        structlog.dev.ConsoleRenderer(colors=True)
        if is_tty
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Pin stdlib loggers (sqlalchemy, uvicorn) below WARNING to keep noise down
    logging.basicConfig(level=getattr(logging, level), stream=sys.stderr)
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
