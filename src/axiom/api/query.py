import asyncio
import json
import logging
import uuid
from typing import Optional

from axiom.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)


async def run_query(
    question: str,
    tenant_id: str = "default_tenant",
    source_id: Optional[str] = None,
) -> None:
    """
    CLI query runner — invokes the full LangGraph agent directly.

    The Temporal ExecutionWorkflow is the post-approval SQL execution path
    used by the web frontend. The CLI runs the graph directly so it works
    for all query types (SQL and app connectors like Gmail).
    """
    from axiom.agent.graph import build_graph

    logger.info("Invoking agent for: '%s' (tenant: %s)", question, tenant_id)

    thread_id = str(uuid.uuid4())
    graph = await build_graph(hitl=False)  # CLI runs full pipeline without HITL interrupt

    initial_state = {
        "question": question,
        "tenant_id": tenant_id,
        "source_id": source_id or "",
        "session_id": thread_id,
        "thread_id": thread_id,
        "attempts": 0,
        "selected_tables": [],
    }
    config = {"configurable": {"thread_id": thread_id}}

    # Run the graph — for SQL queries the graph interrupts before execute_sql
    # waiting for approval; we handle that here interactively.
    result = await graph.ainvoke(initial_state, config=config)

    # Print output
    print("\n" + "=" * 50)
    print(f"QUESTION: {question}")
    if result.get("sql_query"):
        print("-" * 50)
        print(f"SQL:\n{result['sql_query']}")
    if result.get("sql_result"):
        print("-" * 50)
        res = json.loads(result["sql_result"])
        print(f"RESULT ({len(res.get('rows', []))} rows):")
        if res.get("columns"):
            print(" | ".join(res["columns"]))
            for row in res.get("rows", [])[:10]:
                print(" | ".join([str(v) for v in row]))
    if result.get("response_text"):
        print("-" * 50)
        print(result["response_text"])
    if result.get("error"):
        print(f"ERROR: {result['error']}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "How many users?"
    t = sys.argv[2] if len(sys.argv) > 2 else "default_tenant"
    asyncio.run(run_query(q, t))
