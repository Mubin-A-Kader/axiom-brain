"""Domain exceptions and global FastAPI handlers.

Every error returned to a client follows a uniform JSON shape::

    {"error": {"code": "rag.not_found", "message": "...", "request_id": "..."}}

Service code raises typed exceptions (``RAGNotFound``, ``SafetyBlock`` ...)
and never reaches into HTTP — the handlers below translate them.
"""

from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from starlette import status

from axiom.core.logging import get_logger

log = get_logger(__name__)


class AxiomError(Exception):
    code: str = "axiom.error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class NotFoundError(AxiomError):
    code = "axiom.not_found"
    status_code = status.HTTP_404_NOT_FOUND


class ValidationError(AxiomError):
    code = "axiom.validation_error"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class SafetyBlock(AxiomError):
    code = "safety.blocked"
    status_code = status.HTTP_400_BAD_REQUEST


class RateLimitExceeded(AxiomError):
    code = "rate_limit.exceeded"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class UpstreamError(AxiomError):
    """LLM provider, vector DB, or external dependency failed."""

    code = "upstream.error"
    status_code = status.HTTP_502_BAD_GATEWAY


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AxiomError)
    async def axiom_error_handler(request: Request, exc: AxiomError) -> ORJSONResponse:
        request_id = request.headers.get("x-request-id", "-")
        log.warning(
            "request.failed",
            code=exc.code,
            message=exc.message,
            request_id=request_id,
            **exc.extra,
        )
        return ORJSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> ORJSONResponse:
        request_id = request.headers.get("x-request-id", "-")
        log.exception("request.unhandled", request_id=request_id)
        return ORJSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error.",
                    "request_id": request_id,
                }
            },
        )
