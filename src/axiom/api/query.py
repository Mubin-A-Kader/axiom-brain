import asyncio
import json
import logging
import uuid
from typing import Optional

from axiom.agent.graph import build_graph
from axiom.agent.thread import ThreadManager
from axiom.config import settings
import asyncpg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_tenant_rules(tenant_id: str) -> str:
    """Database lookup for tenant-specific SQL rules aggregated across all data sources."""
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            rows = await conn.fetch(
                "SELECT custom_rules FROM data_sources WHERE tenant_id = $1", 
                tenant_id
            )
            rules = [r["custom_rules"] for r in rows if r["custom_rules"]]
            return "\n".join(list(set(rules)))
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Failed to fetch tenant rules: %s", exc)
        return ""

async def run_query(
    question: str, 
    tenant_id: str = "default_tenant", 
    source_id: Optional[str] = None
):
    logger.info(f"Invoking Axiom Brain for: '{question}' (Tenant: {tenant_id}, Source: {source_id or 'Auto'})")
    
    # 1. Initialize core components
    agent = await build_graph()
    thread_mgr = ThreadManager()
    
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    # 2. Prepare state
    history_context, is_stale = await thread_mgr.get_context_injection(thread_id, "")
    
    initial_state = {
        "question": question,
        "selected_tables": [],
        "schema_context": "",
        "few_shot_examples": "",
        "custom_rules": await get_tenant_rules(tenant_id),
        "tenant_id": tenant_id,
        "source_id": source_id, # Can be None for auto-routing
        "sql_query": None,
        "sql_result": None,
        "error": None,
        "attempts": 0,
        "session_id": session_id,
        "thread_id": thread_id,
        "history_context": history_context,
        "is_stale": is_stale,
        "query_type": "", 
    }
    
    # 3. Invoke the graph
    try:
        final_state = await agent.ainvoke(initial_state, config=config)
        
        # Loop to handle multiple interrupts (e.g., during self-correction retries)
        while True:
            curr_state = await agent.aget_state(config)
            if not curr_state.next:
                break
            
            # Show the state to the user (e.g., the generated SQL)
            if "require_approval" in curr_state.next:
                sql_to_run = curr_state.values.get("sql_query", "Unknown SQL")
                print(f"\n[PROPOSED SQL - REQUIRES APPROVAL]:\n{sql_to_run}")
                
                # Interactive prompt
                confirm = input("\nDo you want to execute this SQL? [Y/n]: ").strip().lower()
                if confirm not in ["", "y", "yes"]:
                    print("Execution cancelled by user.")
                    return

            logger.info(f"Agent proceeding to {curr_state.next}...")
            final_state = await agent.ainvoke(None, config=config)

        # 4. Print results
        print("\n" + "="*50)
        print(f"QUESTION: {question}")
        if final_state.get("sql_query"):
            print("-" * 50)
            print(f"GENERATED SQL:\n{final_state['sql_query']}")
        
        if final_state.get("sql_result"):
            print("-" * 50)
            result = json.loads(final_state["sql_result"])
            print(f"RESULT ({len(result.get('rows', []))} rows):")
            # Simple table print
            if result.get("columns"):
                print(" | ".join(result["columns"]))
                for row in result.get("rows", [])[:10]: # Show top 10
                    print(" | ".join([str(v) for v in row]))
                if len(result.get("rows", [])) > 10:
                    print(f"... and {len(result['rows']) - 10} more rows.")
        
        if final_state.get("error"):
            print("-" * 50)
            print(f"ERROR: {final_state['error']}")
        print("="*50 + "\n")
        
    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
    finally:
        # Cleanup connectors
        from axiom.connectors.factory import ConnectorFactory
        await ConnectorFactory.shutdown()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m axiom.api.query 'your question' [tenant_id] [source_id]")
        sys.exit(1)
    
    q = sys.argv[1]
    t = sys.argv[2] if len(sys.argv) > 2 else "default_tenant"
    s = sys.argv[3] if len(sys.argv) > 3 else None
    
    asyncio.run(run_query(q, t, s))
