from typing import Any, Dict

import httpx


class NotebookExecutorClient:
    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        artifact_id: str,
        notebook: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "artifact_id": artifact_id,
            "notebook": notebook,
            "timeout_seconds": self.timeout_seconds,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds + 10) as client:
            response = await client.post(f"{self.base_url}/execute-notebook", json=payload)
            response.raise_for_status()
            return response.json()

