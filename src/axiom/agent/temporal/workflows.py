from datetime import timedelta
from typing import List, Optional
from temporalio import workflow
from axiom.agent.state import SQLAgentState

# Import activities
with workflow.unsafe.imports_passed_through():
    from axiom.agent.temporal.activities import SQLActivities

@workflow.definition
class SQLAgentWorkflow:
    """
    Main orchestrator workflow replacing the LangGraph execution path.
    Implements a stateful, event-sourced agentic loop.
    """
    
    @workflow.run
    async def run(self, initial_state: SQLAgentState) -> SQLAgentState:
        state = initial_state
        
        # 1. Retrieve Schema (Activity)
        state = await workflow.execute_activity(
            SQLActivities.retrieve_schema,
            state,
            start_to_close_timeout=timedelta(minutes=1),
        )
        
        # 2. Plan Query (Activity)
        state = await workflow.execute_activity(
            SQLActivities.plan_query,
            state,
            start_to_close_timeout=timedelta(minutes=1),
        )
        
        # 3. Generate SQL (Activity)
        # Note: This might loop in a real self-correction flow
        max_attempts = 3
        while state.get("attempts", 0) < max_attempts:
            state = await workflow.execute_activity(
                SQLActivities.generate_sql,
                state,
                start_to_close_timeout=timedelta(minutes=2),
            )
            
            # 4. Execute SQL (Activity)
            # This is where we might pause for HITL if required
            # For now, we follow the "Solely through MCP" pattern established in Phase 1
            state = await workflow.execute_activity(
                SQLActivities.execute_sql,
                state,
                start_to_close_timeout=timedelta(minutes=2),
            )
            
            if not state.get("error") or "ZERO_RESULTS" not in state.get("error", ""):
                break
                
            state["attempts"] = state.get("attempts", 0) + 1
            workflow.logger.info(f"Self-correction attempt {state['attempts']}")

        return state
