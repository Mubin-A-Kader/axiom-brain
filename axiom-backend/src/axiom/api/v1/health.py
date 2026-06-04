"""Health checks.

``/health/live``  — Kubernetes liveness probe. Returns 200 once the
                   process is running. No dependency checks.

``/health/ready`` — Readiness probe. Checks Postgres and Redis. Returns
                   503 if either is unhealthy so the load balancer can
                   stop routing traffic.
"""

from fastapi import APIRouter
from fastapi.responses import ORJSONResponse

from axiom.cache.redis_client import get_redis
from axiom.db.session import get_sessionmaker
from sqlalchemy import text

router = APIRouter()


@router.get("/live", summary="Liveness probe")
async def liveness() -> ORJSONResponse:
    return ORJSONResponse({"status": "ok"})


@router.get("/ready", summary="Readiness probe")
async def readiness() -> ORJSONResponse:
    failures: list[str] = []

    # Postgres
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        failures.append(f"postgres: {exc}")

    # Redis
    try:
        await get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"redis: {exc}")

    if failures:
        return ORJSONResponse(status_code=503, content={"status": "degraded", "failures": failures})
    return ORJSONResponse({"status": "ready"})
