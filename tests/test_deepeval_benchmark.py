import os
import json
import pytest
import asyncpg
import re

from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import GEval, BaseMetric
from deepeval.test_case import LLMTestCaseParams
from deepeval.models.llms import LiteLLMModel

import litellm
litellm._turn_on_debug()

from axiom.agent.graph import build_graph
from axiom.config import settings

# 1. Initialize Evaluator Model via LiteLLM
# This aligns with the project's architecture and is more flexible with environment variables
eval_model = LiteLLMModel(
    model="gemini/gemini-2.5-flash",
    api_key=os.environ.get("GEMINI_API_KEY")
)

# 2. Custom Metric: Execution Accuracy
class ExecutionAccuracyMetric(BaseMetric):
    def __init__(self, threshold: float = 1.0):
        self.threshold = threshold
        self.score = 0.0
        self.reason = ""

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        actual_sql = test_case.actual_output
        # Extract the expected SQL from the retrieval context
        expected_sql = ""
        for ctx in test_case.retrieval_context:
            if "Expected SQL: " in ctx:
                expected_sql = ctx.replace("Expected SQL: ", "").strip()
                break
        
        if not expected_sql:
            self.score = 0.0
            self.reason = "Expected SQL not found in retrieval context."
            self.success = False
            return self.score
            
        try:
            conn = await asyncpg.connect(settings.database_url)
            try:
                from decimal import Decimal
                
                def _convert_decimals(data_list):
                    for row in data_list:
                        for k, v in row.items():
                            if isinstance(v, Decimal):
                                row[k] = float(v)
                    return data_list

                # Run the agent's SQL
                actual_rows = await conn.fetch(actual_sql)
                actual_data = _convert_decimals([dict(r) for r in actual_rows])
                
                # Run the gold SQL
                expected_rows = await conn.fetch(expected_sql)
                expected_data = _convert_decimals([dict(r) for r in expected_rows])
                
                # Compare (order-independent comparison)
                is_correct = sorted(json.dumps(actual_data, sort_keys=True)) == sorted(json.dumps(expected_data, sort_keys=True))
                
                self.score = 1.0 if is_correct else 0.0
                self.reason = "Data results match exactly." if is_correct else f"Data mismatch. Expected {len(expected_data)} rows, got {len(actual_data)}."
                self.success = self.score >= self.threshold
                return self.score
            finally:
                await conn.close()
        except Exception as e:
            self.score = 0.0
            self.reason = f"SQL Execution Error: {str(e)}"
            self.success = False
            return self.score

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        self.score = self.score
        self.reason = self.reason
        self.success = self.success
        return self.score

    def is_successful(self) -> bool:
        return self.success

    @property
    def __name__(self):
        return "Execution Accuracy"

# 3. Define the Gold Dataset
GOLD_DATASET = [
    {
        "question": "What is the total revenue from completed orders?",
        "expected_sql": "SELECT SUM(total) FROM orders WHERE status = 'completed'",
        "expected_logic": "Aggregate total from orders where status is 'completed'",
    },
    {
        "question": "Which product category has the most stock?",
        "expected_sql": "SELECT category FROM products GROUP BY category ORDER BY SUM(stock_qty) DESC LIMIT 1",
        "expected_logic": "Group products by category and sum stock_qty, then sort descending",
    },
    {
        "question": "Find the name of the customer who ordered a 'Wireless Mouse'",
        "expected_sql": "SELECT c.name FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE p.name = 'Wireless Mouse'",
        "expected_logic": "Join customers, orders, order_items, and products to filter by product name",
    }
]

# 4. Semantic Metric (LLM-as-a-judge)
sql_correctness_metric = GEval(
    name="SQL Semantic Correctness",
    model=eval_model,
    criteria="Determine if the generated SQL is semantically equivalent to the expected logic and addresses the user question correctly.",
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.RETRIEVAL_CONTEXT],
    threshold=0.7
)

@pytest.mark.asyncio
@pytest.mark.parametrize("test_data", GOLD_DATASET)
async def test_agent_performance(test_data):
    try:
        from chromadb import HttpClient
        HttpClient()
    except Exception:
        pytest.skip("ChromaDB server is unavailable. Skipping end-to-end benchmark.")

    # Initialize agent
    agent = await build_graph()
    
    # Invoke agent
    state = await agent.ainvoke(
        {
            "question": test_data["question"],
            "selected_tables": [],
            "schema_context": "",
            "sql_query": None,
            "sql_result": None,
            "error": None,
            "attempts": 0,
            "thread_id": "benchmark_thread",
            "tenant_id": "default_tenant"
        },
        config={"configurable": {"thread_id": "benchmark_thread"}}
    )    
    actual_sql = state.get("sql_query", "")
    
    # DeepEval Test Case
    test_case = LLMTestCase(
        input=test_data["question"],
        actual_output=actual_sql,
        retrieval_context=[
            f"Expected Logic: {test_data['expected_logic']}",
            f"Expected SQL: {test_data['expected_sql']}"
        ]
    )
    
    # Run metrics
    exec_metric = ExecutionAccuracyMetric()
    
    assert_test(test_case, [sql_correctness_metric, exec_metric])
