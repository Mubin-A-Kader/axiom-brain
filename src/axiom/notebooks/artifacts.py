import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class NotebookArtifactStore:
    """Filesystem-backed artifact store.

    The interface is intentionally small so object storage can replace the
    implementation without changing the agent/API contract.
    """

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or os.getenv("AXIOM_ARTIFACT_ROOT", "/tmp/axiom-artifacts"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _artifact_dir(self, artifact_id: str) -> Path:
        safe_id = "".join(ch for ch in artifact_id if ch.isalnum() or ch in {"-", "_"})
        if not safe_id:
            raise ValueError("Invalid artifact id")
        return self.root / safe_id

    def save(
        self,
        *,
        artifact_id: str,
        tenant_id: str,
        thread_id: str,
        notebook: Dict[str, Any],
        status: str,
        outputs: Optional[list[dict[str, Any]]] = None,
        cells_summary: Optional[list[str]] = None,
        execution_error: Optional[str] = None,
        logs: Optional[str] = None,
    ) -> Dict[str, Any]:
        artifact_dir = self._artifact_dir(artifact_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        notebook_path = artifact_dir / "notebook.ipynb"
        metadata_path = artifact_dir / "artifact.json"

        notebook_path.write_text(json.dumps(notebook, indent=2, default=str), encoding="utf-8")

        artifact = {
            "artifact_id": artifact_id,
            "kind": "notebook",
            "status": status,
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "notebook_url": f"/artifacts/{artifact_id}",
            "download_url": f"/artifacts/{artifact_id}/download",
            "cells_summary": cells_summary or [],
            "outputs": outputs or [],
            "execution_error": execution_error,
            "logs": logs,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
        return self.public_metadata(artifact)

    def load_metadata(self, artifact_id: str) -> Dict[str, Any]:
        metadata_path = self._artifact_dir(artifact_id) / "artifact.json"
        if not metadata_path.exists():
            raise FileNotFoundError(artifact_id)
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def load_notebook(self, artifact_id: str) -> Dict[str, Any]:
        notebook_path = self.notebook_path(artifact_id)
        if not notebook_path.exists():
            raise FileNotFoundError(artifact_id)
        return json.loads(notebook_path.read_text(encoding="utf-8"))

    def notebook_path(self, artifact_id: str) -> Path:
        return self._artifact_dir(artifact_id) / "notebook.ipynb"

    @staticmethod
    def public_metadata(artifact: Dict[str, Any]) -> Dict[str, Any]:
        public = {
            key: value
            for key, value in artifact.items()
            if key not in {"tenant_id", "thread_id", "logs"}
        }
        public["outputs"] = [
            {
                key: value
                for key, value in output.items()
                if key not in {"data_url", "html"}
            }
            for output in public.get("outputs", [])
            if isinstance(output, dict)
        ]
        return public
