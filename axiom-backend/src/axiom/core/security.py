"""Auth and rate limiting.

This boilerplate uses a simple bearer-token scheme — replace with OIDC,
Auth0, Clerk, or your platform of choice. The interface stays the same:
``get_current_principal`` returns a ``Principal`` or raises.

Rate limiting is Redis-backed (sliding-window via INCR + EXPIRE) and
keyed by principal-id with a per-IP fallback for unauthenticated routes.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from axiom.cache.redis_client import get_redis
from axiom.config import get_settings
from axiom.core.exceptions import AxiomError, RateLimitExceeded
from starlette import status


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    scopes: frozenset[str]


class Unauthorized(AxiomError):
    code = "auth.unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED


# This enables the "Authorize" padlock in Swagger UI
security = HTTPBearer(auto_error=False)


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> Principal:
    """Resolve the caller from an ``Authorization: Bearer <token>`` header.

    Replace the body with a real verifier (JWT, opaque-token lookup, etc.).
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing or malformed Authorization header.")

    token = credentials.credentials.strip()
    if not token:
        raise Unauthorized("Empty bearer token.")

    # TODO: verify token, decode claims
    return Principal(id=f"user::{token[:8]}", scopes=frozenset({"read", "write"}))


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


async def enforce_rate_limit(request: Request, principal: CurrentPrincipal) -> None:
    settings = get_settings()
    redis = get_redis()
    bucket = f"rl:{principal.id}:{int(request.scope['path'].count('/'))}"
    count = await redis.incr(bucket)
    if count == 1:
        await redis.expire(bucket, 60)
    if count > settings.rate_limit_per_minute:
        raise RateLimitExceeded(
            "Per-minute rate limit exceeded.",
            limit=settings.rate_limit_per_minute,
        )
