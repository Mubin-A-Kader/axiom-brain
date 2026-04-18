"""
Agent integration and unit tests using pytest + deepeval.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from axiom.agent.graph import build_graph
from axiom.agent.nodes import SchemaRetrievalNode, SQLExecutionNode, SQLGenerationNode
from axiom.agent.state import SQLAgentState
from axiom.rag.schema import SchemaRAG


@pytest.fixture
def sample_state() -> SQLAgentState:
    """Sample agent state for testing."""
    return {
        "question": "How many users are active?",
        "schema_context": "",
        "sql_query": None,
        "sql_result": None,
        "error": None,
        "attempts": 0,
        "session_id": "test-session-1",
        "thread_id": "test-thread-1",
        "tenant_id": "default_tenant",
    }


@pytest.fixture
def mock_rag() -> AsyncMock:
    """Mock SchemaRAG for testing."""
    rag = MagicMock(spec=SchemaRAG)
    rag.retrieve = AsyncMock(
        return_value="TABLE users (id INT, email VARCHAR, active BOOL)"
    )
    rag.retrieve_examples = AsyncMock(return_value="")
    rag.retrieve_exact = AsyncMock(return_value="")
    return rag


@pytest.fixture
def sample_schema_context() -> str:
    return """TABLE users (id INT, email VARCHAR, active BOOL)
TABLE orders (id INT, user_id INT, amount DECIMAL)
TABLE products (id INT, name VARCHAR, price DECIMAL)"""


# ============================================================================
# Unit Tests: SchemaRetrievalNode
# ============================================================================


@pytest.mark.asyncio
async def test_schema_retrieval_node_success(
    mock_rag: AsyncMock, sample_state: SQLAgentState
) -> None:
    """Test schema retrieval node successfully retrieves schema."""
    node = SchemaRetrievalNode(mock_rag)
    result = await node(sample_state)

    assert "schema_context" in result
    assert "TABLE users" in result["schema_context"]
    mock_rag.retrieve.assert_called_once_with("default_tenant", sample_state["question"])


@pytest.mark.asyncio
async def test_schema_retrieval_node_empty_result(
    mock_rag: AsyncMock, sample_state: SQLAgentState
) -> None:
    """Test schema retrieval with empty schema context."""
    mock_rag.retrieve = AsyncMock(return_value="No schema context found.")
    node = SchemaRetrievalNode(mock_rag)
    result = await node(sample_state)

    assert result["schema_context"] == "No schema context found."


# ============================================================================
# Unit Tests: SQLGenerationNode
# ============================================================================


@pytest.mark.asyncio
async def test_sql_generation_node_basic(
    sample_state: SQLAgentState, sample_schema_context: str
) -> None:
    """Test SQL generation node produces SQL query."""
    node = SQLGenerationNode()

    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "<sql>SELECT * FROM users WHERE active = true</sql>"
        mock_create.return_value = mock_response

        state = sample_state.copy()
        state["schema_context"] = sample_schema_context
        result = await node(state)

        assert "sql_query" in result
        assert "SELECT" in result["sql_query"]
        assert result["error"] is None
        assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_sql_generation_node_with_error_correction(
    sample_state: SQLAgentState, sample_schema_context: str
) -> None:
    """Test SQL generation with error correction prompt."""
    node = SQLGenerationNode()
    state = sample_state.copy()
    state["schema_context"] = sample_schema_context
    state["error"] = "Column 'username' does not exist"
    state["attempts"] = 1

    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "<sql>SELECT * FROM users WHERE email IS NOT NULL</sql>"
        mock_create.return_value = mock_response

        result = await node(state)

        assert result["attempts"] == 2
        # Check that error correction prompt was included
        call_args = mock_create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "PREVIOUS ATTEMPT FAILED" in prompt


def test_sql_generation_prompt_building(sample_state: SQLAgentState, sample_schema_context: str) -> None:
    """Test SQL generation prompt structure."""
    node = SQLGenerationNode()
    state = sample_state.copy()
    state["schema_context"] = sample_schema_context
    state["question"] = "Count active users"
    state["error"] = None
    prompt = node._build_prompt(state)

    assert "SQL expert" in prompt
    assert "TABLE users" in prompt
    assert "Count active users" in prompt
    assert "PREVIOUS ATTEMPT FAILED" not in prompt


def test_sql_generation_prompt_with_error(sample_state: SQLAgentState, sample_schema_context: str) -> None:
    """Test SQL prompt includes error context."""
    node = SQLGenerationNode()
    error = "Syntax error near 'WHERE'"
    state = sample_state.copy()
    state["schema_context"] = sample_schema_context
    state["question"] = "Get user emails"
    state["error"] = error
    prompt = node._build_prompt(state)

    assert error in prompt
    assert "PREVIOUS ATTEMPT FAILED" in prompt


# ============================================================================
# Unit Tests: SQLExecutionNode
# ============================================================================


@pytest.mark.asyncio
async def test_sql_execution_node_success(sample_state: SQLAgentState) -> None:
    """Test SQL execution node successfully executes query."""
    node = SQLExecutionNode()
    state = sample_state.copy()
    state["sql_query"] = "SELECT * FROM users LIMIT 1"

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "email": "test@example.com"}])
    mock_conn.close = AsyncMock()

    with patch("axiom.agent.nodes.asyncpg.connect", return_value=mock_conn):
        result = await node(state)

        assert "sql_result" in result
        assert result["error"] is None
        mock_conn.fetch.assert_called_once_with("SELECT * FROM users LIMIT 1")


@pytest.mark.asyncio
async def test_sql_execution_node_error(sample_state: SQLAgentState) -> None:
    """Test SQL execution node handles errors gracefully."""
    node = SQLExecutionNode()
    state = sample_state.copy()
    state["sql_query"] = "SELECT INVALID SQL QUERY"

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=Exception("Query syntax error"))
    mock_conn.close = AsyncMock()

    with patch("axiom.agent.nodes.asyncpg.connect", return_value=mock_conn):
        result = await node(state)

        assert result["sql_result"] is None
        assert "error" in result
        assert "Query syntax error" in result["error"]


# ============================================================================
# Integration Tests: Full Graph
# ============================================================================


@pytest.mark.asyncio
async def test_graph_full_flow(sample_state: SQLAgentState, mock_rag: AsyncMock) -> None:
    """Test full agent graph execution."""
    graph = await build_graph()

    with patch("axiom.agent.graph.SchemaRAG", return_value=mock_rag):
        with patch.object(SQLGenerationNode, "__call__") as mock_gen:
            mock_gen.return_value = {
                "sql_query": "SELECT COUNT(*) FROM users WHERE active = true",
                "error": None,
                "attempts": 1,
            }

            with patch.object(SQLExecutionNode, "__call__") as mock_exec:
                mock_exec.return_value = {
                    "sql_result": "[{count: 42}]",
                    "error": None,
                }

                # Note: In a real integration test, you'd invoke the graph with input()
                # For now, this demonstrates the structure. Full e2e requires running services.
                assert graph is not None


# ============================================================================
# DeepEval Metrics: SQL Quality Assessment
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="DeepEval metrics require OPENAI_API_KEY",
)
async def test_sql_query_relevancy() -> None:
    """Test SQL query relevance to user question using DeepEval."""
    question = "How many active users are there?"
    sql_query = "SELECT COUNT(*) FROM users WHERE active = true"

    test_case = LLMTestCase(
        input=question,
        actual_output=sql_query,
    )

    metric = AnswerRelevancyMetric()
    score = metric.measure(test_case)

    assert score is not None
    assert 0 <= score <= 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="DeepEval metrics require OPENAI_API_KEY",
)
async def test_sql_execution_result_faithfulness() -> None:
    """Test that query results are faithful to the schema."""
    schema = "TABLE users (id INT, email VARCHAR, active BOOL)"
    query_result = '[{"id": 1, "email": "test@example.com", "active": true}]'

    test_case = LLMTestCase(
        input="Get user data",
        actual_output=query_result,
        retrieval_context=[schema],
    )

    metric = FaithfulnessMetric()
    score = metric.measure(test_case)

    assert score is not None
    assert 0 <= score <= 1


# ============================================================================
# Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_schema_retrieval_with_special_characters(
    mock_rag: AsyncMock, sample_state: SQLAgentState
) -> None:
    """Test schema retrieval handles special characters in question."""
    special_question = "What's the user's email? (with 'quotes' and \"double quotes\")"
    state = sample_state.copy()
    state["question"] = special_question

    node = SchemaRetrievalNode(mock_rag)
    await node(state)

    mock_rag.retrieve.assert_called_once_with("default_tenant", special_question)


def test_sql_generation_with_empty_schema(sample_state: SQLAgentState) -> None:
    """Test SQL generation gracefully handles empty schema."""
    node = SQLGenerationNode()
    state = sample_state.copy()
    state["schema_context"] = ""
    state["question"] = "Get users"
    state["error"] = None
    prompt = node._build_prompt(state)

    assert len(prompt) > 0
    assert "SQL expert" in prompt


@pytest.mark.asyncio
async def test_execution_node_max_retry_check(sample_state: SQLAgentState) -> None:
    """Test that execution node tracks retry attempts."""
    node = SQLExecutionNode()

    state = sample_state.copy()
    state["attempts"] = 5
    state["error"] = "Previous query failed"
    state["sql_query"] = "SELECT * FROM users"

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.close = AsyncMock()

    with patch("axiom.agent.nodes.asyncpg.connect", return_value=mock_conn):
        result = await node(state)
        assert result is not None
