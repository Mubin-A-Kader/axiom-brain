import json
import logging

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)


class MemoryManagerNode:
    """Extracts and manages structured semantic memory (filters, joins, errors) from the conversation."""

    def __init__(self) -> None:
        import openai

        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        question = state["question"]
        active_filters = state.get("active_filters", [])
        verified_joins = state.get("verified_joins", [])
        error_log = state.get("error_log", [])
        history_context = state.get("history_context", "")

        prompt = f"""You are the Memory Manager of a State-Aware Text-to-SQL Orchestrator.
Your job is to update the structured semantic memory based on the latest user question and the prior state.

### CURRENT STATE:
Active Filters: {json.dumps(active_filters)}
Verified Joins: {json.dumps(verified_joins)}

### CONVERSATION HISTORY (Context):
{history_context if history_context else "No prior history."}

### NEW USER QUERY:
{question}

### INSTRUCTIONS:
Analyze the new user query in the context of the conversation history and the current state.
1. Determine if this is a completely new topic or a continuation/refinement.
2. If it is a new topic, clear the active filters and verified joins.
3. If it is a refinement, keep the relevant active filters and add any new constraints implied by the new query.
4. Output the updated 'active_filters' and 'verified_joins' as lists of strings.
5. Express filters clearly, e.g., "region = 'Europe'" or "Year=2023".
6. Express verified joins clearly if mentioned or implied by recent successful queries, e.g., "users JOIN orders".

Respond strictly with valid JSON in this format:
{{
  "active_filters": ["filter1", "filter2"],
  "verified_joins": ["join1"]
}}
"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "system", "content": "You output only JSON."}, {"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content.strip()
            result = json.loads(content)
            
            new_filters = result.get("active_filters", active_filters)
            new_joins = result.get("verified_joins", verified_joins)
            
            logger.info(f"Memory Manager updated filters: {new_filters}")
            logger.info(f"Memory Manager updated joins: {new_joins}")

        except Exception as exc:
            logger.warning("Failed to parse memory manager response: %s", exc)
            new_filters = active_filters
            new_joins = verified_joins

        return {
            "active_filters": new_filters,
            "verified_joins": new_joins,
            "error_log": error_log # Pass through to avoid overwriting with None
        }
