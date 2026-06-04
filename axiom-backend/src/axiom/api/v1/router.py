"""v1 API router — registers all sub-routers with their prefixes and tags."""

from fastapi import APIRouter

from axiom.api.v1 import agents, evals, health, rag

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(evals.router, prefix="/evals", tags=["evals"])
