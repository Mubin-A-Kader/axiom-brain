import json
import logging

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)


class QueryPlannerNode:
    """Decompose intent and categorize query as REFINEMENT or NEW_TOPIC."""

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
        schema_context = state.get("schema_context", "")
        custom_rules = state.get("custom_rules", "")

        is_refinement_possible = not (not history_context or "No prior" in history_context or is_stale)

        prompt = f"""You are an elite Data Strategist. Your function is to decompose natural language user queries into a logical execution plan before any code is generated.

### BUSINESS GLOSSARY (SEMANTIC LAYER):
{custom_rules if custom_rules else "None"}

### SCHEMA CONTEXT:
{schema_context}

### RECENT CONVERSATION HISTORY:
{history_context if is_refinement_possible else "No prior history to consider for this query."}

### NEW USER QUERY:
{question}

### INSTRUCTIONS:
1. Determine the Query Type:
   - If the new query relies on the conversation history (e.g., uses pronouns like "their", "it", or relative terms like "in that list", "of those", "top 5 of them"), it is a "REFINEMENT".
   - If it stands alone and introduces a completely new analytical request, it is a "NEW_TOPIC".
   - If {not is_refinement_possible}, it MUST be a "NEW_TOPIC".

2. Define the Logical Blueprint:
   - Break down the user's intent into a step-by-step strategy.
   - Identify required data entities (tables).
   - Define exact mathematical formulas for metrics (especially referring to the BUSINESS GLOSSARY if applicable).
   - Specify grouping and sorting parameters.
   - DO NOT write actual SQL code. Output a clear, structured natural language pseudo-code plan.

Respond strictly with valid JSON in this format:
{{
  "query_type": "REFINEMENT" or "NEW_TOPIC",
  "logical_blueprint": "Step 1: ... \\nStep 2: ... \\nStep 3: ..."
}}"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "system", "content": "You output only JSON."}, {"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content.strip()
            result = json.loads(content)
            query_type = result.get("query_type", "NEW_TOPIC")
            logical_blueprint = result.get("logical_blueprint", "No blueprint generated.")
            
            logger.info(f"Query Planner strategy: {query_type}")
            logger.debug(f"Logical Blueprint: {logical_blueprint}")

        except Exception as exc:
            logger.warning("Failed to generate or parse planner strategy: %s", exc)
            query_type = "NEW_TOPIC" if not is_refinement_possible else "REFINEMENT"
            logical_blueprint = "Fallback: Direct execution requested."

        return {
            "query_type": query_type,
            "logical_blueprint": logical_blueprint
        }
