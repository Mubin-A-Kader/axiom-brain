"""Tests for health endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_liveness(client: AsyncClient) -> None:
    resp = await client.get("/v1/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
