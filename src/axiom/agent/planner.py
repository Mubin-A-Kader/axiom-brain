import json
import logging

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)


class QueryPlannerNode:
    """Categorize query as REFINEMENT or NEW_TOPIC based on history."""

    def __init__(self) -> None:
        import openai

        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        history_context = state.get("history_context", "No prior context.")
        question = state["question"]
        is_stale = state.get("is_stale", True)

        if not history_context or "No prior" in history_context or is_stale:
            return {"query_type": "NEW_TOPIC"}

        prompt = f"""Analyze this query in the context of recent conversation history.

History:
{history_context}

New Query: {question}

Is this query a REFINEMENT (follow-up to previous results) or a NEW_TOPIC (completely different)?

Consider:
- Does it use pronouns like "them", "those", "it"?
- Does it reference "higher", "lower", "more", "less" (comparative)?
- Is it asking for a modification of the previous result?
- Or is it asking about a completely different topic?

Respond with JSON: {{"query_type": "REFINEMENT" or "NEW_TOPIC", "reason": "brief explanation"}}"""

        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        try:
            result = json.loads(response.choices[0].message.content)
            query_type = result.get("query_type", "NEW_TOPIC")
        except Exception as exc:
            logger.warning("Failed to parse planner response: %s", exc)
            query_type = "NEW_TOPIC"

        return {"query_type": query_type}
