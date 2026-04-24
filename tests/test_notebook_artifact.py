import json

import pytest

from axiom.agent.nodes import NotebookArtifactNode
from axiom.agent.state import SQLAgentState


@pytest.fixture
def notebook_state() -> SQLAgentState:
    return {
        "question": "What is the monthly revenue trend?",
        "selected_tables": ["orders"],
        "schema_context": "TABLE orders (id INT, amount DECIMAL, created_at TIMESTAMP)",
        "few_shot_examples": "",
        "custom_rules": "",
        "tenant_id": "test_tenant",
        "source_id": "test_source",
        "db_type": "postgres",
        "sql_query": "SELECT month, revenue FROM monthly_revenue",
        "sql_result": json.dumps({
            "columns": ["month", "revenue"],
            "rows": [["2023-01", 1000], ["2023-02", 1200], ["2023-03", 1500]],
        }),
        "error": None,
        "attempts": 0,
        "session_id": "s1",
        "thread_id": "t1",
        "history_context": "",
        "is_stale": False,
        "query_type": "NEW_TOPIC",
        "artifact": None,
        "layout": "default",
        "action_bar": [],
        "llm_model": None,
        "response_text": None,
        "agent_thought": None,
        "critic_feedback": None,
        "logical_blueprint": None,
        "active_filters": [],
        "verified_joins": [],
        "error_log": [],
        "negative_constraints": [],
        "probing_options": [],
        "confirmed_tables": [],
        "history_tables": [],
    }


@pytest.mark.asyncio
async def test_notebook_artifact_node_generates_completed_artifact(
    tmp_path,
    monkeypatch,
    notebook_state,
):
    monkeypatch.setattr("axiom.agent.nodes.settings.artifact_root", str(tmp_path))
    node = NotebookArtifactNode()

    async def fake_execute(**kwargs):
        notebook = kwargs["notebook"]
        notebook["cells"][1]["outputs"] = [
            {"output_type": "stream", "name": "stdout", "text": "Rows: 3\n"}
        ]
        return {
            "status": "completed",
            "notebook": notebook,
            "outputs": [{"cell_index": 1, "type": "stream", "name": "stdout", "text": "Rows: 3\n"}],
            "execution_error": None,
            "logs": "",
        }

    monkeypatch.setattr(node._executor, "execute", fake_execute)

    result = await node(notebook_state)

    artifact = result["artifact"]
    assert artifact["kind"] == "notebook"
    assert artifact["status"] == "completed"
    assert artifact["notebook_url"].startswith("/artifacts/")
    assert artifact["download_url"].endswith("/download")
    assert artifact["outputs"][0]["text"] == "Rows: 3\n"


@pytest.mark.asyncio
async def test_notebook_artifact_node_returns_failed_artifact_when_executor_fails(
    tmp_path,
    monkeypatch,
    notebook_state,
):
    monkeypatch.setattr("axiom.agent.nodes.settings.artifact_root", str(tmp_path))
    node = NotebookArtifactNode()

    async def fake_execute(**kwargs):
        raise RuntimeError("executor offline")

    monkeypatch.setattr(node._executor, "execute", fake_execute)

    result = await node(notebook_state)

    artifact = result["artifact"]
    assert artifact["kind"] == "notebook"
    assert artifact["status"] == "failed"
    assert "executor offline" in artifact["execution_error"]
