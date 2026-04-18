import json
import logging
from datetime import date, datetime
from decimal import Decimal

import asyncpg

from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


def _to_json(rows: list, cols: list[str]) -> str:
    def _convert(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return v
    return json.dumps({"columns": cols, "rows": [[_convert(v) for v in row] for row in rows]})


class SchemaRetrievalNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag

    async def __call__(self, state: SQLAgentState) -> dict:
        context = await self._rag.retrieve(state["question"])
        return {"schema_context": context}


class SQLGenerationNode:
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    def _build_prompt(
        self, schema_context: str, question: str, error: str | None, history_context: str = "", query_type: str = "NEW_TOPIC"
    ) -> str:
        base = f"""You are a SQL expert. Database schema:

{schema_context}

---
{history_context}

---
Return ONLY a valid SQL SELECT query. No explanation, no markdown fences.

{"If the user uses pronouns or relative descriptors, refer to the conversation history above." if "No prior" not in history_context else ""}

Query Type: {query_type}
{("If REFINEMENT, use previous SQL as a CTE or subquery to build upon it." if query_type == "REFINEMENT" else "This is a new topic. Generate fresh SQL.") if query_type else ""}

Question: {question}"""
        if error:
            base += f"\n\nPrevious attempt failed with: {error}\nFix the query."
        return base

    async def __call__(self, state: SQLAgentState) -> dict:
        prompt = self._build_prompt(
            state["schema_context"],
            state["question"],
            state.get("error"),
            state.get("history_context", ""),
            state.get("query_type", "NEW_TOPIC"),
        )
        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_temperature,
        )
        sql = response.choices[0].message.content.strip()
        return {"sql_query": sql, "error": None, "attempts": state["attempts"] + 1}


class SQLExecutionNode:
    def __init__(self, thread_mgr=None) -> None:
        self._thread_mgr = thread_mgr

    async def __call__(self, state: SQLAgentState) -> dict:
        sql = (state["sql_query"] or "").strip()
        if not sql.upper().startswith("SELECT"):
            return {"sql_result": None, "error": "Only SELECT queries are allowed."}
        try:
            conn = await asyncpg.connect(settings.database_url)
            try:
                rows = await conn.fetch(sql)
                if not rows:
                    return {"sql_result": json.dumps({"columns": [], "rows": []}), "error": None}
                cols = list(rows[0].keys())
                data = [list(row.values()) for row in rows]
                return {"sql_result": _to_json(data, cols), "error": None}
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("SQL execution error: %s", exc)
            return {"sql_result": None, "error": str(exc)}

        if self._thread_mgr and state.get("sql_query"):
            await self._thread_mgr.save_turn(
                state["thread_id"],
                state["question"],
                state["sql_query"],
                state.get("sql_result", ""),
            )
