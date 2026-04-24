import json
import traceback
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Axiom Notebook Executor", version="0.1.0")


class ExecuteNotebookRequest(BaseModel):
    tenant_id: str
    thread_id: str
    artifact_id: str
    notebook: Dict[str, Any]
    timeout_seconds: int = 60


def _summarize_outputs(notebook: Dict[str, Any]) -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for cell_index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        for output in cell.get("outputs", []):
            output_type = output.get("output_type")
            if output_type == "stream":
                text = output.get("text", "")
                outputs.append({
                    "cell_index": cell_index,
                    "type": "stream",
                    "name": output.get("name", "stdout"),
                    "text": text if len(text) < 4000 else text[:4000] + "\n...[truncated]",
                })
            elif output_type in {"display_data", "execute_result"}:
                data = output.get("data", {})
                if "image/png" in data:
                    outputs.append({
                        "cell_index": cell_index,
                        "type": "image",
                        "mime": "image/png",
                        "data_url": "data:image/png;base64," + data["image/png"],
                    })
                elif "text/html" in data:
                    html = data["text/html"]
                    safe_html = html if len(html) < 200_000 else html[:200_000] + "\n<!-- truncated -->"
                    outputs.append({
                        "cell_index": cell_index,
                        "type": "html",
                        "html": safe_html,
                    })
                elif "text/plain" in data:
                    text = data["text/plain"]
                    outputs.append({
                        "cell_index": cell_index,
                        "type": "text",
                        "text": text if len(text) < 4000 else text[:4000] + "\n...[truncated]",
                    })
            elif output_type == "error":
                outputs.append({
                    "cell_index": cell_index,
                    "type": "error",
                    "ename": output.get("ename"),
                    "evalue": output.get("evalue"),
                })
    return outputs


def _validate_notebook_source(notebook: Dict[str, Any]) -> Optional[str]:
    blocked = ["socket.", "subprocess", "os.system", "shutil.rmtree", "open('/", "open(\"/"]
    for cell in notebook.get("cells", []):
        source = cell.get("source", "")
        if cell.get("cell_type") == "code" and any(token in source for token in blocked):
            return "Notebook contains blocked execution token."
    return None


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/execute-notebook")
async def execute_notebook(req: ExecuteNotebookRequest) -> Dict[str, Any]:
    validation_error = _validate_notebook_source(req.notebook)
    if validation_error:
        return {
            "status": "failed",
            "notebook": req.notebook,
            "outputs": [],
            "execution_error": validation_error,
            "logs": validation_error,
        }

    try:
        import nbformat
        from nbclient import NotebookClient

        notebook = nbformat.from_dict(req.notebook)
        client = NotebookClient(
            notebook,
            timeout=req.timeout_seconds,
            kernel_name="python3",
            allow_errors=False,
        )
        executed = client.execute()
        executed_notebook = json.loads(nbformat.writes(executed))
        return {
            "status": "completed",
            "notebook": executed_notebook,
            "outputs": _summarize_outputs(executed_notebook),
            "execution_error": None,
            "logs": "",
        }
    except Exception as exc:
        tb = traceback.format_exc(limit=8)
        return {
            "status": "failed",
            "notebook": req.notebook,
            "outputs": [],
            "execution_error": str(exc),
            "logs": tb,
        }
