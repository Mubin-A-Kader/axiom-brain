"""ASGI middleware: request ID and timing.

``RequestIDMiddleware`` ensures every request has an ``X-Request-ID``
header (honoring a client-provided value or generating one) and binds it
to ``structlog`` contextvars so every log line emitted while handling
the request carries the same ID. ``TimingMiddleware`` adds a
``server-timing`` response header for client-side perf debugging.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        clear_contextvars()
        bind_contextvars(request_id=request_id, path=request.url.path, method=request.method)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["server-timing"] = f"total;dur={elapsed_ms:.1f}"
        return response
