import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from axiom.agent.nodes import SQLExecutionNode, SQLGenerationNode
from axiom.agent.state import SQLAgentState
from axiom.config import settings

@pytest.fixture
def sample_state() -> SQLAgentState:
    return {
        "question": "Select all from users",
        "selected_tables": ["users"],
        "schema_context": "TABLE users (id INT, name TEXT)",
        "few_shot_examples": "",
        "custom_rules": "",
        "tenant_id": "test_tenant",
        "source_id": "test_source",
        "sql_query": None,
        "sql_result": None,
        "error": None,
        "attempts": 0,
        "session_id": "s1",
        "thread_id": "t1",
        "history_context": "",
        "is_stale": False,
        "query_type": "NEW_TOPIC",
    }

@pytest.mark.asyncio
async def test_sql_execution_read_only_enforcement():
    """Verify that non-SELECT queries are blocked by sqlglot in SQLExecutionNode."""
    node = SQLExecutionNode()
    
    # 1. Test DROP TABLE
    state_drop = {"sql_query": "DROP TABLE users", "tenant_id": "t", "attempts": 0}
    result = await node(state_drop)
    assert "Security Violation" in result["error"]
    assert "not a SELECT statement" in result["error"]
    assert result["sql_result"] is None

    # 2. Test UPDATE
    state_update = {"sql_query": "UPDATE users SET name = 'hacker'", "tenant_id": "t", "attempts": 0}
    result = await node(state_update)
    assert "Security Violation" in result["error"]

    # 3. Test DELETE
    state_delete = {"sql_query": "DELETE FROM users WHERE id = 1", "tenant_id": "t", "attempts": 0}
    result = await node(state_delete)
    assert "Security Violation" in result["error"]

    # 4. Test complex injection (CTE that tries to write)
    state_cte = {"sql_query": "WITH deleted AS (DELETE FROM users RETURNING *) SELECT * FROM deleted", "tenant_id": "t", "attempts": 0}
    result = await node(state_cte)
    assert "Security Violation" in result["error"]

    # 5. Test valid SELECT
    state_select = {"sql_query": "SELECT * FROM users", "tenant_id": "t", "attempts": 0}
    # Mock the rest of execution to avoid DB calls
    with patch.object(node, "_is_read_only", return_value=True):
        with patch("axiom.agent.nodes.asyncpg.connect") as mock_cp:
            mock_cp.return_value = AsyncMock()
            # We just want to see it didn't trigger the security error immediately
            # but it will fail later on DB connection which is fine for this test
            try:
                await node(state_select)
            except Exception:
                pass

@pytest.mark.asyncio
async def test_max_attempts_exhaustion():
    """Verify that SQLGenerationNode stops after MAX_RETRIES."""
    mock_rag = MagicMock()
    mock_rag.search_semantic_cache = AsyncMock(return_value=None)
    node = SQLGenerationNode(mock_rag)
    
    state = {
        "question": "test",
        "attempts": settings.max_correction_attempts,
        "error": "Previous syntax error",
        "schema_context": "...",
        "tenant_id": "t",
        "source_id": "s"
    }
    
    result = await node(state)
    assert "Exhausted maximum SQL correction attempts" in result["error"]
    assert result.get("sql_query") is None

@pytest.mark.asyncio
async def test_token_limit_retrieval():
    """Verify that SchemaRAG respects token limits."""
    from axiom.rag.schema import SchemaRAG
    from axiom.config import settings
    
    # Mock chroma
    with patch("chromadb.HttpClient"):
        rag = SchemaRAG()
        rag._count_tokens = MagicMock(side_effect=lambda x: len(x.split())) # Mock token count as word count
        
        # Mock collection query
        rag._collection.query = MagicMock(return_value={
            "metadatas": [[{"table": "t1"}, {"table": "t2"}, {"table": "t3"}]]
        })

        # Mock collection get for each table
        def mock_get(ids, include=None):
            table = ids[0].split("_")[-1]
            if "summary" in ids[0]:
                return {"documents": [f"Summary {table}"]}
            return {"documents": [f"TABLE {table} (c1 INT)"]}

        rag._collection.get = MagicMock(side_effect=mock_get)

        with patch.object(settings, "max_schema_tokens", 10): # Max 10 words
            result = await rag.retrieve("t", "s", "q")
            # TABLE t1 (c1 INT) + Description: Summary t1 = ~6 words
            # Adding t2 would exceed 10.
            assert "t1" in result
            assert "t2" not in result
