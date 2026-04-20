import pytest
from unittest.mock import AsyncMock, patch

from axiom.agent.nodes import SQLGenerationNode
from axiom.agent.state import SQLAgentState

@pytest.fixture
def sample_state() -> SQLAgentState:
    return {
        "question": "Show me the best customers",
        "schema_context": "TABLE customers (id INT, name VARCHAR, spend DECIMAL)",
        "sql_query": None,
        "sql_result": None,
        "error": None,
        "attempts": 0,
        "session_id": "test-session-1",
        "thread_id": "test-thread-1",
        "tenant_id": "default_tenant",
        "source_id": "test_source",
        "query_type": "NEW_TOPIC"
    }

@pytest.mark.asyncio
async def test_semantic_cache_hit(sample_state: SQLAgentState):
    """Test that SQLGenerationNode bypasses the LLM on a semantic cache hit."""
    mock_rag = AsyncMock()
    # Simulate a cache hit
    mock_rag.search_semantic_cache.return_value = {
        "sql": "SELECT * FROM customers ORDER BY spend DESC LIMIT 10",
        "distance": 0.05
    }
    
    node = SQLGenerationNode(rag=mock_rag)
    
    with patch.object(node._client.chat.completions, "create") as mock_create:
        result = await node(sample_state)
        
        # Verify the LLM was not called
        mock_create.assert_not_called()
        
        # Verify the cached SQL was returned
        assert result["sql_query"] == "SELECT * FROM customers ORDER BY spend DESC LIMIT 10"
        assert result["error"] is None
        
        # Verify RAG was checked
        mock_rag.search_semantic_cache.assert_called_once_with("default_tenant", "test_source", "Show me the best customers")

@pytest.mark.asyncio
async def test_semantic_cache_miss(sample_state: SQLAgentState):
    """Test that SQLGenerationNode calls the LLM on a semantic cache miss."""
    mock_rag = AsyncMock()
    # Simulate a cache miss
    mock_rag.search_semantic_cache.return_value = None
    
    node = SQLGenerationNode(rag=mock_rag)
    
    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_response = AsyncMock()
        mock_response.choices[0].message.content = "<sql>SELECT name FROM customers</sql>"
        mock_create.return_value = mock_response
        
        result = await node(sample_state)
        
        # Verify the LLM was called
        mock_create.assert_called_once()
        
        # Verify the generated SQL was returned
        assert result["sql_query"] == "SELECT name FROM customers"
        
        # Verify RAG was checked
        mock_rag.search_semantic_cache.assert_called_once_with("default_tenant", "test_source", "Show me the best customers")

@pytest.mark.asyncio
async def test_semantic_cache_skip_on_refinement(sample_state: SQLAgentState):
    """Test that SQLGenerationNode skips semantic cache check if query_type is REFINEMENT."""
    mock_rag = AsyncMock()
    
    node = SQLGenerationNode(rag=mock_rag)
    
    # Change query type to REFINEMENT
    state = sample_state.copy()
    state["query_type"] = "REFINEMENT"
    
    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_response = AsyncMock()
        mock_response.choices[0].message.content = "<sql>SELECT name FROM customers</sql>"
        mock_create.return_value = mock_response
        
        await node(state)
        
        # Verify RAG cache was NOT checked
        mock_rag.search_semantic_cache.assert_not_called()
        
        # Verify LLM was called
        mock_create.assert_called_once()
