import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
from axiom.agent.nodes import DataStorytellingNode
from axiom.agent.state import SQLAgentState

@pytest.fixture
def mock_viz_state() -> SQLAgentState:
    return {
        "question": "What is the monthly revenue trend?",
        "selected_tables": ["orders"],
        "schema_context": "TABLE orders (id INT, amount DECIMAL, created_at TIMESTAMP)",
        "few_shot_examples": "",
        "custom_rules": "",
        "tenant_id": "test_tenant",
        "source_id": "test_source",
        "sql_query": "SELECT date_trunc('month', created_at) as month, sum(amount) as revenue FROM orders GROUP BY 1",
        "sql_result": json.dumps({
            "columns": ["month", "revenue"],
            "rows": [["2023-01-01", 1000], ["2023-02-01", 1200], ["2023-03-01", 1500]]
        }),
        "error": None,
        "attempts": 0,
        "session_id": "s1",
        "thread_id": "t1",
        "history_context": "",
        "is_stale": False,
        "query_type": "NEW_TOPIC",
        "visualization": None,
    }

@pytest.mark.asyncio
async def test_data_storytelling_node_generates_spec(mock_viz_state):
    """Verify that DataStorytellingNode produces a valid JSON spec with an insight title."""
    node = DataStorytellingNode()
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "x_axis": "month",
        "y_axis": "revenue",
        "plot_type": "line",
        "title": "Monthly revenue grew steadily by 50% over Q1"
    })
    
    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        
        result = await node(mock_viz_state)
        
        assert result["visualization"] is not None
        spec = json.loads(result["visualization"])
        assert spec["x_axis"] == "month"
        assert spec["plot_type"] == "line"
        assert "grew steadily" in spec["title"]

@pytest.mark.asyncio
async def test_data_storytelling_node_handles_no_data(mock_viz_state):
    """Verify that DataStorytellingNode returns None if no rows exist."""
    node = DataStorytellingNode()
    state = mock_viz_state.copy()
    state["sql_result"] = json.dumps({"columns": ["c1"], "rows": []})
    
    result = await node(state)
    assert result["visualization"] is None

@pytest.mark.asyncio
async def test_data_storytelling_node_handles_scalar_indicator(mock_viz_state):
    """Verify that DataStorytellingNode handles scalar values as indicators."""
    node = DataStorytellingNode()
    state = mock_viz_state.copy()
    state["question"] = "What is the total revenue?"
    state["sql_result"] = json.dumps({"columns": ["total"], "rows": [[5000]]})
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "x_axis": None,
        "y_axis": "total",
        "plot_type": "indicator",
        "title": "Total revenue reached $5,000"
    })
    
    with patch.object(node._client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        result = await node(state)
        spec = json.loads(result["visualization"])
        assert spec["plot_type"] == "indicator"
