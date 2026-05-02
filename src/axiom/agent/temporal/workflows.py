from datetime import timedelta
from typing import Optional

from temporalio import workflow

from axiom.agent.state import SQLAgentState

with workflow.unsafe.imports_passed_through():
    from axiom.agent.temporal.activities import SQLActivities
    from axiom.config import settings


def _should_correct(state: SQLAgentState) -> str:
    """
    Mirrors graph.py::_should_correct exactly.
    Pure deterministic function — safe to run inside the workflow.
    """
    error = state.get("error")
    sql_result = state.get("sql_result")
    attempts = state.get("attempts", 0)

    if sql_result:
        return "visualize"
    if attempts >= settings.max_correction_attempts:
        return "synthesize"
    if error and (
        "Exhausted maximum SQL correction" in error
        or "permission denied" in error.lower()
    ):
        return "synthesize"
    if error and "ZERO_RESULTS" in error:
        return "critic"
    if error and "does not exist" in error.lower() and attempts <= 1:
        return "discovery"
    if error:
        return "critic"
    return "synthesize"


@workflow.defn
class ExecutionWorkflow:
    """
    Durable execution phase — runs after the user approves the generated SQL.

    LangGraph handles everything up to and including SQL generation (streamed
    to the frontend). This workflow takes over at the Approve boundary and
    durably runs the execution + self-correction + synthesis pipeline.

    Temporal provides per-activity retries, heartbeats, execution history,
    and time-travel debugging for this critical write path.
    """

    def __init__(self) -> None:
        self._state: Optional[SQLAgentState] = None

    @workflow.query
    def get_state(self) -> Optional[SQLAgentState]:
        return self._state

    @workflow.run
    async def run(self, state: SQLAgentState) -> SQLAgentState:
        self._state = state

        # Short LLM calls: 3 min, no heartbeat needed
        act = {"start_to_close_timeout": timedelta(minutes=3)}
        # DB + heavy calls: 5 min with 30s heartbeat so Temporal detects hangs
        long_act = {
            "start_to_close_timeout": timedelta(minutes=5),
            "heartbeat_timeout": timedelta(seconds=30),
        }

        # ── Execute + self-correction loop ────────────────────────────────────
        while True:
            state = await workflow.execute_activity(
                SQLActivities.execute_sql, state, **long_act
            )
            self._state = state
            
            # Validate data quality (null checks)
            state = await workflow.execute_activity(
                SQLActivities.validate_data, state, **act
            )
            self._state = state

            next_step = _should_correct(state)
            workflow.logger.info(
                "thread=%s attempts=%s → %s",
                state.get("thread_id"), state.get("attempts"), next_step,
            )

            if next_step == "visualize":
                # ── Generate declarative visualization manifest ───────────
                state = await workflow.execute_activity(
                    SQLActivities.visualize, state, **act
                )
                self._state = state

                # Also build the optional notebook artifact in the background
                state = await workflow.execute_activity(
                    SQLActivities.build_notebook, state, **long_act
                )
                self._state = state
                break

            if next_step == "synthesize":
                break

            if next_step == "discovery":
                state = await workflow.execute_activity(
                    SQLActivities.run_discovery, state, **long_act
                )
                self._state = state
                state = await workflow.execute_activity(
                    SQLActivities.generate_sql, state, **act
                )
                self._state = state
                continue

            if next_step == "critic":
                state = await workflow.execute_activity(
                    SQLActivities.run_critic, state, **long_act
                )
                self._state = state
                state = await workflow.execute_activity(
                    SQLActivities.generate_sql, state, **act
                )
                self._state = state
                continue

            break  # safety

        # ── Synthesize response ───────────────────────────────────────────────
        state = await workflow.execute_activity(
            SQLActivities.synthesize_response, state, **act
        )
        self._state = state

        return state
