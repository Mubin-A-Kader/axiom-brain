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

Is this query a REFINEMENT (follow-up to the previous query/result) or a NEW_TOPIC (asking about something different)?

Guidelines:
- If the query uses phrases like "in that", "of those", "from that list", or refers to the previous result using pronouns ("their", "them", "it", "his", "her", "that", "those") or relative terms ("more", "higher", "latest", "recent"), it is ALWAYS a REFINEMENT.
- Even if a new name or entity is mentioned (e.g., "anyone with name bob"), if it is contextualized by "in that" or refers to the previous results, it is a REFINEMENT.
- If the query is completely unrelated and stands alone without needing prior context, it is a NEW_TOPIC.
- If in doubt, choose NEW_TOPIC.

Respond with ONLY JSON: {{"query_type": "REFINEMENT" or "NEW_TOPIC", "reason": "brief explanation"}}"""

        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        try:
            content = response.choices[0].message.content
            import re
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                result = json.loads(match.group(0))
                query_type = result.get("query_type", "NEW_TOPIC")
            else:
                query_type = "NEW_TOPIC"
        except Exception as exc:
            logger.warning("Failed to parse planner response: %s", exc)
            query_type = "NEW_TOPIC"

        return {"query_type": query_type}
