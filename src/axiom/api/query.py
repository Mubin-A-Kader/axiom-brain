import asyncio
import json
import logging
import uuid
import os
from typing import Optional

from temporalio.client import Client
from axiom.agent.temporal.workflows import SQLAgentWorkflow
from axiom.agent.thread import ThreadManager
from axiom.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_query(
    question: str, 
    tenant_id: str = "default_tenant", 
    source_id: Optional[str] = None
):
    logger.info(f"Invoking Agentic Stack for: '{question}' (Tenant: {tenant_id})")
    
    # 1. Connect to Temporal
    try:
        client = await Client.connect("localhost:7233")
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}. Ensure docker is running.")
        return

    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    
    # 2. Prepare state
    initial_state = {
        "question": question,
        "selected_tables": [],
        "tenant_id": tenant_id,
        "source_id": source_id or "default_source",
        "session_id": session_id,
        "thread_id": thread_id,
        "attempts": 0,
    }
    
    # 3. Start Workflow
    try:
        handle = await client.start_workflow(
            SQLAgentWorkflow.run,
            initial_state,
            id=f"cli-query-{thread_id}",
            task_queue="sql-agent-tasks",
        )
        
        print(f"Workflow started. ID: {handle.id}")
        
        # 4. Polling for HITL (Approval) or Completion
        while True:
            state = await handle.query("get_state")
            
            # Check if workflow is finished
            desc = await handle.describe()
            if desc.status != 1: # Not Running
                final_state = await handle.result()
                break
                
            # Check if we are in the "pending approval" state
            # (Generated SQL exists but no result yet)
            if state and state.get("sql_query") and not state.get("sql_result") and not state.get("error"):
                print(f"\n[PROPOSED SQL]:\n{state['sql_query']}")
                confirm = input("\nDo you want to execute this SQL? [Y/n]: ").strip().lower()
                
                if confirm in ["", "y", "yes"]:
                    await handle.signal("approve", True)
                    # Wait for execution to finish
                    final_state = await handle.result()
                    break
                else:
                    await handle.signal("approve", False)
                    print("Execution rejected.")
                    return
            
            await asyncio.sleep(1)

        # 5. Print results
        print("\n" + "="*50)
        print(f"QUESTION: {question}")
        if final_state.get("sql_query"):
            print("-" * 50)
            print(f"GENERATED SQL:\n{final_state['sql_query']}")
        
        if final_state.get("sql_result"):
            print("-" * 50)
            result = json.loads(final_state["sql_result"])
            print(f"RESULT ({len(result.get('rows', []))} rows):")
            if result.get("columns"):
                print(" | ".join(result["columns"]))
                for row in result.get("rows", [])[:10]:
                    print(" | ".join([str(v) for v in row]))
        
        if final_state.get("error"):
            print("-" * 50)
            print(f"ERROR: {final_state['error']}")
        print("="*50 + "\n")
        
    except Exception as e:
        logger.exception(f"Agent execution failed: {e}")

if __name__ == "__main__":
    import sys
    # Basic arg parsing if run directly
    q = sys.argv[1] if len(sys.argv) > 1 else "How many users?"
    t = sys.argv[2] if len(sys.argv) > 2 else "default_tenant"
    asyncio.run(run_query(q, t))
