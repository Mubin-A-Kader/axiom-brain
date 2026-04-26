"""
n8n client using the internal /rest/ API with session-based auth.

n8n's public /api/v1/ requires an API key manually created in the UI.
The internal /rest/ API uses email/password login and returns a session cookie —
this is what n8n's own UI uses, so it's stable and works without UI setup.
"""
import logging
import secrets
import copy
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

import httpx

from axiom.config import settings

logger = logging.getLogger(__name__)

_WORKFLOW_TEMPLATE = {
    "name": "",
    "active": True,
    "nodes": [
        {
            "id": "webhook-node",
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 1.1,
            "position": [0, 0],
            "parameters": {
                "httpMethod": "POST",
                "path": "",
                "responseMode": "responseNode",
                "options": {},
            },
            "webhookId": "",
        },
        {
            "id": "fetch-node",
            "name": "Fetch Data",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [240, 0],
            "parameters": {
                "method": "GET",
                "url": "={{ $json.body.fetch_url }}",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "",
                "options": {},
            },
            "credentials": {},
        },
        {
            "id": "respond-node",
            "name": "Respond",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1,
            "position": [480, 0],
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($input.all().map(i => i.json)) }}",
                "options": {},
            },
        },
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Fetch Data", "type": "main", "index": 0}]]},
        "Fetch Data": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
}


class N8nClient:
    def __init__(self) -> None:
        self._base = settings.n8n_url.rstrip("/")
        self._user = settings.n8n_user
        self._password = settings.n8n_password

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx.AsyncClient]:
        """
        Async context manager that yields an authenticated httpx client.
        Login and all API calls share one client so cookies flow automatically.
        On first boot, runs /rest/owner/setup before logging in.
        """
        async with httpx.AsyncClient(
            base_url=self._base,
            headers={"Content-Type": "application/json"},
            timeout=30,
        ) as client:
            r = await client.post(
                "/rest/login",
                json={"emailOrLdapLoginId": self._user, "password": self._password},
            )

            if r.status_code == 400:
                logger.info("n8n has no owner account. Running /rest/owner/setup …")
                setup = await client.post(
                    "/rest/owner/setup",
                    json={
                        "email": self._user,
                        "password": self._password,
                        "firstName": "Axiom",
                        "lastName": "Bot",
                    },
                )
                if not setup.is_success:
                    raise RuntimeError(f"n8n owner setup failed ({setup.status_code}): {setup.text}")
                logger.info("n8n owner account created.")
                r = setup  # setup response also sets the auth token

            elif not r.is_success:
                raise RuntimeError(
                    f"n8n login failed ({r.status_code}). Check N8N_USER / N8N_PASSWORD in .env."
                )

            # n8n sets the session as an HttpOnly+Secure cookie. When running over plain
            # HTTP (internal Docker network) the Secure flag prevents httpx from forwarding
            # it automatically. Extract the raw token and inject it as a Cookie header so
            # it's always sent regardless of the scheme.
            token = r.cookies.get("n8n-auth") or r.headers.get("set-cookie", "").split("n8n-auth=")[-1].split(";")[0]
            if token and "n8n-auth=" not in token:
                client.headers["Cookie"] = f"n8n-auth={token}"

            yield client

    async def create_credential(self, name: str, credential_type: str, data: Dict[str, Any]) -> str:
        async with self._session() as c:
            r = await c.post("/rest/credentials", json={"name": name, "type": credential_type, "data": data})
            r.raise_for_status()
            cred_id: str = str(r.json()["data"]["id"])
            logger.info("Created n8n credential %s (type=%s)", cred_id, credential_type)
            return cred_id

    async def delete_credential(self, credential_id: str) -> None:
        async with self._session() as c:
            r = await c.delete(f"/rest/credentials/{credential_id}")
            if r.status_code not in (200, 404):
                r.raise_for_status()

    async def create_workflow(
        self,
        source_name: str,
        credential_type: str,
        credential_id: str,
        webhook_secret: str,
        default_fetch_url: str = "",
    ) -> Dict[str, Any]:
        wf = copy.deepcopy(_WORKFLOW_TEMPLATE)
        webhook_path = secrets.token_urlsafe(16)
        wf["name"] = f"axiom:{source_name}"

        webhook_node = next(n for n in wf["nodes"] if n["id"] == "webhook-node")
        webhook_node["parameters"]["path"] = webhook_path
        webhook_node["webhookId"] = webhook_path

        fetch_node = next(n for n in wf["nodes"] if n["id"] == "fetch-node")
        fetch_node["parameters"]["nodeCredentialType"] = credential_type
        fetch_node["credentials"] = {credential_type: {"id": credential_id, "name": source_name}}
        # Bake the URL in as a static string when known at provision time,
        # avoiding any expression ambiguity in n8n's webhook context.
        if default_fetch_url:
            fetch_node["parameters"]["url"] = default_fetch_url

        async with self._session() as c:
            r = await c.post("/rest/workflows", json=wf)
            r.raise_for_status()
            created = r.json()["data"]
            workflow_id: str = str(created["id"])
            version_id: str = str(created["versionId"])

            r2 = await c.post(
                f"/rest/workflows/{workflow_id}/activate",
                json={"versionId": version_id},
            )
            r2.raise_for_status()

        webhook_url = f"{self._base}/webhook/{webhook_path}"
        logger.info("Provisioned n8n workflow %s → %s", workflow_id, webhook_url)
        return {"workflow_id": workflow_id, "webhook_url": webhook_url, "webhook_secret": webhook_secret}

    async def delete_workflow(self, workflow_id: str) -> None:
        async with self._session() as c:
            r = await c.delete(f"/rest/workflows/{workflow_id}")
            if r.status_code not in (200, 404):
                r.raise_for_status()

    async def get_oauth_url(self, credential_type: str, credential_name: str, data: Dict[str, Any] = {}) -> Dict[str, Any]:
        async with self._session() as c:
            r = await c.post(
                "/rest/credentials",
                json={"name": credential_name, "type": credential_type, "data": data},
            )
            r.raise_for_status()
            credential_id: str = str(r.json()["data"]["id"])

            # n8n returns 200 JSON {"data": "<google-oauth-url>"} or a 302 redirect.
            r2 = await c.get("/rest/oauth2-credential/auth", params={"id": credential_id}, follow_redirects=False)
            if r2.status_code in (301, 302, 307, 308):
                auth_url: str = r2.headers.get("location", "")
            else:
                r2.raise_for_status()
                body = r2.json()
                auth_url = body.get("data") or body.get("authUrl", "")

        return {"credential_id": credential_id, "auth_url": auth_url}

    async def health(self) -> bool:
        try:
            async with self._session() as c:
                r = await c.get("/rest/workflows", params={"limit": 1})
                return r.status_code == 200
        except Exception:
            return False
