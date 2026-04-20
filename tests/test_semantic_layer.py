import pytest
from axiom.agent.nodes import SQLGenerationNode
from axiom.rag.schema import SchemaRAG

@pytest.mark.asyncio
async def test_semantic_layer_enforcement():
    rag = SchemaRAG()
    node = SQLGenerationNode(rag)

    # Simulate a state where a custom metric "Net Revenue" is defined in the semantic layer
    state = {
        "question": "What is our Net Revenue by month?",
        "selected_tables": ["orders"],
        "schema_context": "TABLE orders (id INT, amount DECIMAL, created_at TIMESTAMP, status TEXT)",
        "few_shot_examples": "",
        "custom_rules": '[{"name": "Net Revenue", "formula": "SUM(amount) FILTER (WHERE status = \'paid\')", "description": "Total revenue minus refunds/unpaid."}]',
        "tenant_id": "test_tenant",
        "source_id": "test_source",
        "sql_query": None,
        "error": None,
        "attempts": 0,
        "history_context": "",
        "query_type": "NEW_TOPIC"
    }

    # Generate the prompt
    prompt = await node._build_prompt(state)

    # Verify the custom rules (Business Glossary) are injected correctly
    assert "BUSINESS GLOSSARY (SEMANTIC LAYER):" in prompt
    assert "Net Revenue" in prompt
    assert "SUM(amount) FILTER (WHERE status = 'paid')" in prompt
    
    # Verify the enforcement instructions are present
    assert "SEMANTIC LAYER ENFORCEMENT:" in prompt
    assert "MUST use the EXACT SQL formula provided in the glossary" in prompt

