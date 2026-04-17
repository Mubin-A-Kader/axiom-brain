import logging
from mcp import ClientSession, StdioServerParameters

from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


class SchemaRetrievalNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag

    async def __call__(self, state: SQLAgentState) -> dict:
        context = await self._rag.retrieve(state["question"])
        return {"schema_context": context}


class SQLGenerationNode:
    def __init__(self) -> None:
        import litellm
        self._litellm = litellm

    def _build_prompt(self, schema_context: str, question: str, error: str | None) -> str:
        base = f"""You are a SQL expert. Database schema:

{schema_context}

---
Return ONLY a valid SQL SELECT query. No explanation, no markdown fences.

Question: {question}"""
        if error:
            base += f"\n\nPrevious attempt failed with: {error}\nFix the query."
        return base

    async def __call__(self, state: SQLAgentState) -> dict:
        prompt = self._build_prompt(
            state["schema_context"],
            state["question"],
            state.get("error"),
        )
        response = await self._litellm.acompletion(
            model=settings.llm_model,
            api_base=settings.litellm_url,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_temperature,
        )
        sql = response.choices[0].message.content.strip()
        return {"sql_query": sql, "error": None, "attempts": state["attempts"] + 1}


class SQLExecutionNode:
    def __init__(self, connector_script: str) -> None:
        self._connector_script = connector_script

    async def __call__(self, state: SQLAgentState) -> dict:
        server_params = StdioServerParameters(
            command="python",
            args=[self._connector_script],
        )
        try:
            async with ClientSession(*server_params) as session:  # type: ignore[arg-type]
                result = await session.call_tool("run_query", {"sql": state["sql_query"]})
            return {"sql_result": result.content[0].text, "error": None}
        except Exception as exc:
            logger.warning("SQL execution error: %s", exc)
            return {"sql_result": None, "error": str(exc)}
