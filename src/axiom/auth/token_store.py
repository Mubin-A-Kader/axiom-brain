import json
import base64
import hashlib
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from axiom.config import settings

logger = logging.getLogger(__name__)


def _fernet(tenant_id: str):
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(f"{settings.connector_master_key}:{tenant_id}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(tenant_id: str, data: dict) -> str:
    return _fernet(tenant_id).encrypt(json.dumps(data).encode()).decode()


def _decrypt(tenant_id: str, ciphertext: str) -> dict:
    return json.loads(_fernet(tenant_id).decrypt(ciphertext.encode()).decode())


async def save(tenant_id: str, connector: str, credentials: dict) -> None:
    encrypted = _encrypt(tenant_id, credentials)
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            """
            INSERT INTO app_connections (tenant_id, connector, credentials)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id, connector)
            DO UPDATE SET credentials = EXCLUDED.credentials,
                          status      = 'connected',
                          updated_at  = NOW()
            """,
            tenant_id, connector, encrypted,
        )
    finally:
        await conn.close()


async def load(tenant_id: str, connector: str) -> dict:
    conn = await asyncpg.connect(settings.database_url)
    try:
        row = await conn.fetchrow(
            "SELECT credentials FROM app_connections WHERE tenant_id = $1 AND connector = $2",
            tenant_id, connector,
        )
    finally:
        await conn.close()
    if not row:
        raise ValueError(
            f"No connection found for '{connector}' (tenant: {tenant_id}). "
            f"Run: axiom connect {connector} --tenant {tenant_id}"
        )
    return _decrypt(tenant_id, row["credentials"])


async def remove(tenant_id: str, connector: str) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            "DELETE FROM app_connections WHERE tenant_id = $1 AND connector = $2",
            tenant_id, connector,
        )
    finally:
        await conn.close()


async def list_connected(tenant_id: str) -> list[dict]:
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT connector, status, connected_at
            FROM app_connections
            WHERE tenant_id = $1
            ORDER BY connected_at
            """,
            tenant_id,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def maybe_refresh(manifest, credentials: dict, tenant_id: str) -> dict:
    """Refresh the access token if it will expire within the manifest's refresh margin."""
    expires_at = credentials.get("expires_at")
    if not expires_at:
        return credentials

    remaining = expires_at - time.time()
    if remaining > manifest.token_refresh_margin_seconds:
        return credentials

    if not credentials.get("refresh_token"):
        logger.warning(
            "Token for '%s' (tenant %s) is expiring and has no refresh_token.",
            manifest.name, tenant_id,
        )
        return credentials

    logger.info("Refreshing token for '%s' (tenant %s)", manifest.name, tenant_id)

    oauth = manifest.oauth2
    client_id = os.environ.get(oauth.client_id_env, "")
    client_secret = os.environ.get(oauth.client_secret_env, "")

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": credentials["refresh_token"],
        "client_id": client_id,
        "client_secret": client_secret,
    })

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            oauth.token_url,
            content=data.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        new_tokens = resp.json()

    credentials["access_token"] = new_tokens["access_token"]
    if "refresh_token" in new_tokens:
        credentials["refresh_token"] = new_tokens["refresh_token"]
    credentials["expires_at"] = time.time() + new_tokens.get("expires_in", 3600)

    await save(tenant_id, manifest.name, credentials)
    return credentials
