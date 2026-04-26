import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from axiom.config import settings
from axiom.security.auth import verify_token
from axiom.api.onboard import run_ingestion

router = APIRouter()


class OAuthUrlRequest(BaseModel):
    connector: str
    tenant_id: str
    source_id: str


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _get_fernet():
    from cryptography.fernet import Fernet

    return Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(settings.connector_master_key.encode()).digest())
    )


@router.post("/api/oauth/url")
async def get_oauth_url(req: OAuthUrlRequest, user_id: str = Depends(verify_token)) -> dict:
    from axiom.connectors.apps.factory import AppConnectorFactory

    try:
        try:
            manifest = AppConnectorFactory.get_manifest(req.connector)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to get manifest: {e}")
            raise HTTPException(status_code=400, detail=f"Unknown connector {req.connector}")

        oauth = manifest.oauth2
        if not oauth:
            import logging
            logging.getLogger(__name__).error(f"Connector {req.connector} has no oauth2 config")
            raise HTTPException(
                status_code=400, detail=f"Connector {req.connector} does not support OAuth2"
            )

        client_id = os.environ.get(oauth.client_id_env, "")
        if not client_id:
            import logging
            logging.getLogger(__name__).error(f"Missing {oauth.client_id_env} environment variable")
            raise HTTPException(
                status_code=500, detail=f"Missing {oauth.client_id_env} environment variable"
            )

        verifier, challenge = _pkce_pair()

        state_dict = {
            "tenant_id": req.tenant_id,
            "source_id": req.source_id,
            "connector": req.connector,
            "verifier": verifier,
        }

        fernet = _get_fernet()
        state = fernet.encrypt(json.dumps(state_dict).encode()).decode()

        redirect_uri = "http://localhost:8080/api/oauth/callback"

        params = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(oauth.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        return {"url": f"{oauth.auth_url}?{params}"}
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unexpected error in get_oauth_url")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/oauth/callback")
async def oauth_callback(code: str, state: str, background_tasks: BackgroundTasks):
    try:
        fernet = _get_fernet()
        state_dict = json.loads(fernet.decrypt(state.encode()).decode())

        tenant_id = state_dict["tenant_id"]
        source_id = state_dict["source_id"]
        connector = state_dict["connector"]
        verifier = state_dict["verifier"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state token")

    from axiom.connectors.apps.factory import AppConnectorFactory

    manifest = AppConnectorFactory.get_manifest(connector)
    oauth = manifest.oauth2
    client_id = os.environ.get(oauth.client_id_env, "")
    client_secret = os.environ.get(oauth.client_secret_env, "")

    redirect_uri = "http://localhost:8080/api/oauth/callback"

    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        }
    ).encode()

    try:
        req = urllib.request.Request(oauth.token_url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())

        creds = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": time.time() + tokens.get("expires_in", 3600),
            "token_type": tokens.get("token_type", "Bearer"),
            "scope": tokens.get("scope", ""),
        }

        from axiom.auth.token_store import save
        await save(tenant_id, connector, creds)

        # App connectors don't have SQL schemas to ingest, so we just register them as active
        import asyncpg
        conn = await asyncpg.connect(settings.database_url)
        try:
            await conn.execute("""
                INSERT INTO data_sources (source_id, tenant_id, name, description, db_url, db_type, status)
                VALUES ($1, $2, $1, $3, $4, $5, 'active')
                ON CONFLICT (source_id) DO UPDATE 
                SET status = 'active', error_message = NULL, db_url = EXCLUDED.db_url;
            """, source_id, tenant_id, f"{manifest.display_name} Connection", f"{connector}://oauth", connector)
        finally:
            await conn.close()

        return HTMLResponse('<html><body><h2>Axiom: App connected!</h2><p>You may close this window and return to Axiom.</p><script>window.close();</script></body></html>')
    except Exception as e:
        return HTMLResponse(
            f"<html><body><h2>Error connecting app</h2><p>{str(e)}</p></body></html>",
            status_code=400,
        )
