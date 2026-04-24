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
    Implements a stateful, event-sourced agentic loop with HITL support.
    """
    def __init__(self) -> None:
        self._approved = False
        self._rejected = False
        self._state: Optional[SQLAgentState] = None

    @workflow.signal
    def approve(self, approved: bool) -> None:
        if approved:
            self._approved = True
        else:
            self._rejected = True
            
    @workflow.query
    def get_state(self) -> Optional[SQLAgentState]:
        return self._state
    
    @workflow.run
    async def run(self, initial_state: SQLAgentState) -> SQLAgentState:
        self._state = initial_state
        state = self._state
        
        # 1. Retrieve Schema
        state = await workflow.execute_activity(
            SQLActivities.retrieve_schema,
            state,
            start_to_close_timeout=timedelta(minutes=1),
        )
        self._state = state
        
        # 2. Plan & Generate
        state = await workflow.execute_activity(
            SQLActivities.generate_sql,
            state,
            start_to_close_timeout=timedelta(minutes=2),
        )
        self._state = state

        # 3. Human-in-the-Loop Interrupt
        # In this architecture, we pause for approval before the first execution
        # of any generated SQL to ensure Zero Trust human verification.
        await workflow.wait_condition(lambda: self._approved or self._rejected)
        
        if self._rejected:
            state["error"] = "Query rejected by user."
            return state

        # 4. Execute SQL with Self-Correction Loop
        max_attempts = 3
        while state.get("attempts", 0) < max_attempts:
            state = await workflow.execute_activity(
                SQLActivities.execute_sql,
                state,
                start_to_close_timeout=timedelta(minutes=2),
            )
            
            # If successful or terminal error, stop
            if not state.get("error") or "ZERO_RESULTS" not in state.get("error", ""):
                if "Syntax Error" not in str(state.get("error")):
                    break
                
            state["attempts"] = state.get("attempts", 0) + 1
            workflow.logger.info(f"Self-correction attempt {state['attempts']}")

        return state
