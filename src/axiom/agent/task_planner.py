"""
TaskPlannerNode  — LLM-driven multi-step task decomposition.
TaskExecutorNode — Sequential executor that runs tasks in dependency order.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import asyncpg
import openai

from axiom.agent.prompt_registry import registry as _prompt_registry
from axiom.agent.state import SQLAgentState
from axiom.agent.tasks import AgentType, AxiomTask, TaskPlan, TaskState
from axiom.config import settings

logger = logging.getLogger(__name__)


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# TaskPlannerNode
# ---------------------------------------------------------------------------

class TaskPlannerNode:
    """
    Receives the user question + tenant's active data sources.
    Calls the LLM to produce an ordered AxiomTask list.
    Writes the plan into state as task_plan: list[dict].
    """

    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def _fetch_sources(self, tenant_id: str) -> list[dict[str, str]]:
        try:
            conn = await asyncpg.connect(settings.database_url)
            try:
                rows = await conn.fetch(
                    "SELECT source_id, name, db_type, description "
                    "FROM data_sources "
                    "WHERE tenant_id = $1 AND status = 'active'",
                    tenant_id,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("TaskPlannerNode: failed to fetch sources: %s", exc)
            return []

    async def __call__(self, state: SQLAgentState) -> dict[str, Any]:
        question = state.get("question", "")
        tenant_id = state.get("tenant_id", "")
        history_context = state.get("history_context", "") or "No prior history."

        sources = await self._fetch_sources(tenant_id)
        if not sources:
            # No sources registered — emit a single-task fallback plan
            fallback = AxiomTask(
                id="task_1",
                content=question,
                agent_type=AgentType.SQL,
                source_id=state.get("source_id"),
                depends_on=[],
            )
            return {"task_plan": [fallback.to_dict()]}

        sources_block = "\n".join(
            f"- source_id={s['source_id']}  name={s['name']}  type={s['db_type']}"
            + (f"  description={s['description']}" if s.get("description") else "")
            for s in sources
        )

        system_msg = _prompt_registry.render_system("task_planner")
        context_msg = _prompt_registry.render_context(
            "task_planner",
            question=question,
            sources_block=sources_block,
            history_context=history_context,
        )

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = _strip_think(response.choices[0].message.content or "")
            parsed = json.loads(raw)
            tasks_raw: list[dict] = parsed.get("tasks", [])

            tasks = [AxiomTask.from_dict(t) for t in tasks_raw]
            if not tasks:
                raise ValueError("LLM returned empty task list")

            logger.info(
                "TaskPlannerNode: decomposed into %d tasks: %s",
                len(tasks),
                [t.id for t in tasks],
            )
            return {"task_plan": [t.to_dict() for t in tasks]}

        except Exception as exc:
            logger.warning("TaskPlannerNode: decomposition failed (%s), using single-task fallback", exc)
            fallback = AxiomTask(
                id="task_1",
                content=question,
                agent_type=AgentType.SQL,
                source_id=sources[0]["source_id"] if sources else state.get("source_id"),
                depends_on=[],
            )
            return {"task_plan": [fallback.to_dict()]}


# ---------------------------------------------------------------------------
# TaskExecutorNode
# ---------------------------------------------------------------------------

class TaskExecutorNode:
    """
    Runs tasks from task_plan in dependency order.

    SQL tasks delegate to LakeWorker (schema RAG → generation → execution).
    SYNTHESIS tasks call the LLM with all dependency results as context.

    Results accumulate in TaskPlan.tasks[*].result and are written back
    to state as task_plan (updated) plus sql_result / response_text for the
    downstream ResponseSynthesizerNode.
    """

    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict[str, Any]:
        raw_plan = state.get("task_plan") or []
        if not raw_plan:
            return {"response_text": "No task plan was generated.", "sql_result": None}

        plan = TaskPlan.from_state(raw_plan)
        question = state.get("question", "")
        tenant_id = state.get("tenant_id", "")
        thread_id = state.get("thread_id", "")
        llm_model = state.get("llm_model") or settings.llm_model
        history_context = state.get("history_context", "")

        # Execute tasks round-by-round until the plan is complete or stuck
        max_rounds = len(plan.tasks) + 2
        for _ in range(max_rounds):
            if plan.is_complete():
                break
            ready = plan.ready()
            if not ready:
                # Dependency cycle or all remaining tasks already failed
                break

            # Run all currently-ready tasks concurrently
            await asyncio.gather(
                *[
                    self._run_task(
                        task=t,
                        plan=plan,
                        question=question,
                        tenant_id=tenant_id,
                        thread_id=thread_id,
                        llm_model=llm_model,
                        history_context=history_context,
                        state=state,
                    )
                    for t in ready
                ],
                return_exceptions=True,
            )

        # Assemble final outputs for downstream nodes
        sql_tasks = [t for t in plan.tasks if t.agent_type == AgentType.SQL and t.state == TaskState.DONE]
        synthesis_tasks = [t for t in plan.tasks if t.agent_type == AgentType.SYNTHESIS and t.state == TaskState.DONE]

        # Prefer synthesis narrative; fall back to SQL result summary
        response_text: Optional[str] = None
        sql_result: Optional[str] = None

        if synthesis_tasks:
            response_text = synthesis_tasks[-1].result
        if sql_tasks:
            # Surface the largest SQL result set as the primary data payload
            best = max(sql_tasks, key=lambda t: self._row_count(t.result))
            sql_result = best.result

        failed = [t for t in plan.tasks if t.state == TaskState.FAILED]
        if failed and not sql_tasks and not synthesis_tasks:
            errors = "; ".join(f"{t.id}: {t.error}" for t in failed)
            response_text = f"Task execution failed: {errors}"

        logger.info(
            "TaskExecutorNode: %d done, %d failed, %d skipped",
            sum(1 for t in plan.tasks if t.state == TaskState.DONE),
            sum(1 for t in plan.tasks if t.state == TaskState.FAILED),
            sum(1 for t in plan.tasks if t.state == TaskState.SKIPPED),
        )

        return {
            "task_plan": plan.to_state(),
            "sql_result": sql_result,
            "response_text": response_text,
        }

    # ── Task dispatch ────────────────────────────────────────────────────────

    async def _run_task(
        self,
        *,
        task: AxiomTask,
        plan: TaskPlan,
        question: str,
        tenant_id: str,
        thread_id: str,
        llm_model: str,
        history_context: str,
        state: SQLAgentState,
    ) -> None:
        task.state = TaskState.RUNNING
        try:
            if task.agent_type == AgentType.SQL:
                result = await self._run_sql_task(
                    task=task,
                    plan=plan,
                    tenant_id=tenant_id,
                    llm_model=llm_model,
                    history_context=history_context,
                )
            else:
                result = await self._run_synthesis_task(
                    task=task,
                    plan=plan,
                    question=question,
                    llm_model=llm_model,
                    state=state,
                )
            task.result = result
            task.state = TaskState.DONE
            logger.info("Task %s completed (%s)", task.id, task.agent_type)
        except Exception as exc:
            task.error = str(exc)
            task.state = TaskState.FAILED
            logger.warning("Task %s failed: %s", task.id, exc)

    # ── SQL task — delegates to LakeWorker ──────────────────────────────────

    async def _run_sql_task(
        self,
        *,
        task: AxiomTask,
        plan: TaskPlan,
        tenant_id: str,
        llm_model: str,
        history_context: str,
    ) -> str:
        from axiom.agent.lake_worker import LakeWorker
        from axiom.rag.schema import SchemaRAG

        if not task.source_id:
            raise ValueError(f"SQL task {task.id!r} has no source_id")

        # Enrich the task content with context from dependencies
        dep_context = plan.results_context(task)
        enriched_question = task.content
        if dep_context:
            enriched_question = (
                f"{task.content}\n\n"
                f"Use the following prior results as context:\n{dep_context}"
            )

        rag = SchemaRAG()
        worker = LakeWorker(task.source_id, rag, self._client)
        semaphore = asyncio.Semaphore(1)

        result = await worker.run(
            question=enriched_question,
            tenant_id=tenant_id,
            llm_model=llm_model,
            semaphore=semaphore,
            history_context=history_context,
            query_type="NEW_TOPIC",
        )

        if result.error and not result.sql_result:
            raise RuntimeError(result.error)

        return result.sql_result or ""

    # ── Synthesis task — LLM correlation ────────────────────────────────────

    async def _run_synthesis_task(
        self,
        *,
        task: AxiomTask,
        plan: TaskPlan,
        question: str,
        llm_model: str,
        state: SQLAgentState,
    ) -> str:
        dep_context = plan.results_context(task)

        prompt = (
            f"You are a Senior Data Analyst synthesising query results from multiple data sources.\n\n"
            f"### ORIGINAL QUESTION:\n{question}\n\n"
            f"### YOUR SPECIFIC SUB-TASK:\n{task.content}\n\n"
            f"### DATA FROM PRIOR TASKS:\n{dep_context or 'No prior results available.'}\n\n"
            "### INSTRUCTIONS:\n"
            "1. Address the sub-task directly using the data provided.\n"
            "2. If comparing across sources, highlight similarities, differences, and key insights.\n"
            "3. Quantify where possible (percentages, ratios, deltas).\n"
            "4. Write 3-5 concise sentences. No raw JSON, no table names, no SQL.\n\n"
            "Analysis:"
        )

        response = await self._client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return _strip_think(response.choices[0].message.content or "")

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row_count(result_json: Optional[str]) -> int:
        if not result_json:
            return 0
        try:
            return json.loads(result_json).get("total_count", 0)
        except Exception:
            return 0
