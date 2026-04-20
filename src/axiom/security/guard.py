import logging

import httpx

from axiom.config import settings

logger = logging.getLogger(__name__)


class LakeraGuard:
    _URL = "https://api.lakera.ai/v2/guard/results"

    async def is_safe(self, text: str) -> bool:
        """Returns True if text passes the semantic firewall (Lakera Guard)."""
        if not settings.lakera_api_key:
            return True  # guard disabled in local dev

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._URL,
                    headers={"Authorization": f"Bearer {settings.lakera_api_key}"},
                    json={"messages": [{"role": "user", "content": text}]},
                    timeout=5.0,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.debug("Lakera response: %s", data)
                result = data.get("results", [{}])[0]
                # v2 uses "flagged", v1 used same — fall back to safe if missing
                return result.get("flagged", False) is False
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("Lakera Guard unavailable: %s — allowing request", exc)
            return True
