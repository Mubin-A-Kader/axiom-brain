import httpx

from axiom.config import settings


class LakeraGuard:
    _URL = "https://api.lakera.ai/v1/prompt_injection"

    async def is_safe(self, user_input: str) -> bool:
        """Returns True if input passes the semantic firewall."""
        if not settings.lakera_api_key:
            return True  # guard disabled in local dev

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._URL,
                headers={"Authorization": f"Bearer {settings.lakera_api_key}"},
                json={"input": user_input},
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()["results"][0]["flagged"] is False
