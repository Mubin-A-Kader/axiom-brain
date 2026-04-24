import pytest
import json
import asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from axiom.api.app import app
from axiom.agent.temporal.workflows import SQLAgentWorkflow
from axiom.agent.temporal.activities import SQLActivities
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager

@pytest.mark.asyncio
async def test_e2e_agentic_workflow_success():
    """
    E2E Test: Validates the entire flow from API to Temporal to MCP Hub.
    """
    async with await WorkflowEnvironment.start_local() as env:
        # 1. Setup Mock Dependencies
        mock_rag = MagicMock(spec=SchemaRAG)
        mock_rag.retrieve = AsyncMock(return_value="TABLE users (id INT, name TEXT)")
        mock_rag.retrieve_examples = AsyncMock(return_value="")
        
        mock_thread_mgr = MagicMock(spec=ThreadManager)
        mock_thread_mgr.save_turn = AsyncMock()
        mock_thread_mgr.get_context_injection = AsyncMock(return_value=("", False))
        mock_thread_mgr.get_cached_result = AsyncMock(return_value=None)
        
        activities = SQLActivities(mock_rag, mock_thread_mgr)
        
        # CRITICAL: Must match the queue name used in axiom/api/app.py
        task_queue = "sql-agent-tasks"
        
        # 2. Run Worker in background
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[SQLAgentWorkflow],
            activities=[
                activities.retrieve_schema,
                activities.plan_query,
                activities.generate_sql,
                activities.execute_sql,
            ],
        ):
            # 3. Mock LLM and Sandbox
            with patch("axiom.agent.nodes.SQLGenerationNode.__call__", new_callable=AsyncMock) as mock_gen, \
                 patch("axiom.agent.temporal.sandbox.SandboxedMCPServer.run_in_sandbox", new_callable=AsyncMock) as mock_sandbox, \
                 patch("axiom.api.app._guard.is_safe", new_callable=AsyncMock, return_value=True):
                
                mock_gen.return_value = {"sql_query": "SELECT * FROM users", "agent_thought": "Thinking..."}
                mock_sandbox.return_value = {"columns": ["id", "name"], "rows": [[1, "Alice"]]}
                
                # 4. Patch Temporal Client and Auth
                with patch("axiom.api.app._temporal_client", env.client):
                    from axiom.security.auth import verify_token
                    app.dependency_overrides[verify_token] = lambda: "test_user"
                    
                    # Mock MCP Registry to avoid real SSE connections
                    with patch("axiom.connectors.mcp.registry.mcp_registry.get_connector", new_callable=AsyncMock) as mock_mcp_get:
                        mock_session = MagicMock()
                        mock_session.call_tool = AsyncMock()
                        mock_res = MagicMock()
                        mock_res.content = [MagicMock(text="Mocked Schema Context")]
                        mock_session.call_tool.return_value = mock_res
                        mock_connector = MagicMock()
                        mock_connector._session = mock_session
                        mock_mcp_get.return_value = mock_connector

                        # Mock RAG and ThreadMgr instances already created at module level
                        with patch("axiom.api.app._rag", mock_rag), \
                             patch("axiom.api.app._thread_mgr", mock_thread_mgr):
                            
                            transport = httpx.ASGITransport(app=app)
                            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                                # 5. Execute Request
                                response = await ac.post("/query", json={
                                    "question": "Show all users",
                                    "tenant_id": "test_tenant",
                                    "session_id": "test_session",
                                    "thread_id": "test_thread_e2e"
                                })
                                
                                assert response.status_code == 200
                                data = response.json()
                                # The workflow is designed to pause for approval, 
                                # so it should return pending_approval
                                assert data["status"] == "pending_approval"
                                assert "SELECT * FROM users" in data["sql"]
                                
                    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_zero_trust_pep_blocking():
    from axiom.connectors.mcp.hub import hub
    authorized = await hub.pep.authorize_tool_call(
        subject_did="did:axiom:agent:knowledge_retrieval:parent:123",
        tool_name="run_query", 
        server_name="postgres",      
        arguments={"tenant_id": "test"}
    )
    assert authorized is False
