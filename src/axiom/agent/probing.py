import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import asyncpg

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_COMMAND_PREFIXES = (
    "CONFIRMED_SOURCE:",
    "CONFIRMED_DATABASE:",
    "REJECTED_INTENT:",
    "CLARIFIED_INTENT:",
)

_AMBIGUITY_PROMPT = """
You are a data analyst deciding whether a user's question needs clarification BEFORE
running a SQL query. Be conservative - only ask when truly necessary.

==============================
USER QUESTION:
{question}
==============================

NOTE ON CLARIFICATIONS:
The user may have already provided answers to some dimensions in brackets like [Dimension=Value].
If a dimension is already present in the question string, it is RESOLVED. DO NOT ask for it again.
Only probe for REMAINING critical dimensions that are still missing.

==============================
CONVERSATION HISTORY (if any):
{history}
==============================

A question is ambiguous ONLY when it meets ALL of these criteria:
1. A critical dimension is completely absent (not just vague)
2. Different reasonable interpretations would produce radically different SQL
3. The question cannot be reasonably answered with standard business assumptions

DO NOT probe for:
- Ranking questions ("top N X by Y") - already fully specified
- Questions with clear objects AND metrics (e.g. "companies by profit", "revenue by region")
- Questions where time range can default to "all time" or "most recent period"
- Questions about charts or visualizations - format is separate from data intent
- Any question where a reasonable default exists

ONLY probe for genuine ambiguity like:
- "Show me performance" - no metric whatsoever
- "Compare results" - nothing specified at all
- "What happened this month?" - completely undefined subject

Return JSON:
{{
  "ambiguity_score": <integer 0-100>,
  "clarification_questions": [
    {{
      "id": "cq_0",
      "dimension": "<dimension_name>",
      "question": "<one concise clarifying question>",
      "options": ["<option1>", "<option2>", "<option3>", "<option4>"]
    }}
  ]
}}

Rules:
- Score 0-74: clear enough -> return clarification_questions as []
- Score 75-100: genuinely ambiguous -> at most 2 questions
- Options must be concrete realistic values, never generic placeholders
- When in doubt, score below 75 and let the SQL generator handle it
"""

class ProbingOption(BaseModel):
    id: str
    business_name: str
    description: str
    sample_data: List[Dict[str, Any]]
    table_name: str

class ClarificationUI(BaseModel):
    question: str
    options: List[ProbingOption]

class IntentProberNode:
    """
    Analyzes schema ambiguity, pulls samples, and prepares a Comparison Card for the user.
    """
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def _get_samples(self, db_url: str, table: str) -> List[Dict[str, Any]]:
        try:
            # Handle schema-qualified names by quoting parts
            if "." in table:
                quoted_table = ".".join([f'"{p}"' for p in table.split(".")])
            else:
                quoted_table = f'"{table}"'
                
            conn = await asyncpg.connect(db_url, timeout=10)
            try:
                rows = await conn.fetch(f'SELECT * FROM {quoted_table} LIMIT 2')
                return [dict(r) for r in rows]
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"Probing failed to fetch samples for {table}: {e}")
            return []

    async def __call__(self, state: SQLAgentState) -> dict:
        selected_tables = state.get("selected_tables", [])
        confirmed_tables = state.get("confirmed_tables", [])
        history_tables = state.get("history_tables", [])
        
        # We only want to probe for NEW ambiguities. 
        # Exclude already confirmed or historically successful tables from the probing set.
        unconfirmed_tables = [
            t for t in selected_tables 
            if t not in confirmed_tables and t not in history_tables
        ]
        
        source_id = state.get("source_id")
        tenant_id = state["tenant_id"]
        question = state["question"]
        
        # If the user already confirmed tables, trust that choice and narrow
        # selected_tables to only what was confirmed so the SQL generator
        # doesn't get confused by the other candidates.
        if confirmed_tables:
            narrowed = [t for t in selected_tables if t in confirmed_tables]
            return {
                "probing_options": [],
                "selected_tables": narrowed if narrowed else confirmed_tables,
            }

        # MANDATORY PROBE: If we have 2 or more UNCONFIRMED tables, we SHOW them. No more guessing.
        if not unconfirmed_tables or len(unconfirmed_tables) < 2:
            return {"probing_options": []}

        # 1. Connectivity
        try:
            cp_conn = await asyncpg.connect(settings.database_url, timeout=5)
            try:
                row = await cp_conn.fetchrow("SELECT db_url FROM data_sources WHERE source_id = $1", source_id)
                if not row: return {"probing_options": []}
                db_url = row["db_url"]
            finally:
                await cp_conn.close()
        except Exception as e:
            logger.error(f"Prober failed to connect to control plane: {e}")
            return {"probing_options": []}

        logger.info(f"Mandatory Probing Triggered for unconfirmed tables: {unconfirmed_tables}")

        probing_options = []
        # Sample the top 3 candidates
        for i, table in enumerate(unconfirmed_tables[:3]):
            samples = await self._get_samples(db_url, table)
            
            translate_prompt = f"""Translate this database table name and its sample data into a clear Business Entity name and description.
            Table: {table}
            Sample Data: {json.dumps(samples, default=str)}
            
            Return JSON: {{"business_name": "...", "description": "..."}}"""
            
            try:
                res = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=[{"role": "user", "content": translate_prompt}],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                meta = json.loads(res.choices[0].message.content)
                
                probing_options.append({
                    "id": f"opt_{i}",
                    "business_name": meta["business_name"],
                    "description": meta["description"],
                    "sample_data": samples,
                    "table_name": table
                })
            except Exception:
                continue

        return {"probing_options": probing_options}


class QuestionAmbiguityNode:
    """
    Pre-routing ambiguity probe.
    Scores the question (0-100) for ambiguity before routing.
    If the score is high, returns clarification questions.
    Skipped for system commands and refinements.
    """

    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        question = state.get("question", "")

        # Don't probe deterministic system commands
        if any(question.startswith(p) for p in _SYSTEM_COMMAND_PREFIXES):
            return {"clarification_questions": []}

        # Don't probe if the user is refining a previous successful query
        history = state.get("history_context", "")
        if history and "No prior" not in history and len(question) < 120:
            has_prior_sql = "SQL:" in history and "Result:" in history
            if has_prior_sql:
                return {"clarification_questions": []}

        prompt = _AMBIGUITY_PROMPT.format(
            question=question,
            history=history or "None",
        )

        try:
            res = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(res.choices[0].message.content)
            score = int(data.get("ambiguity_score", 0))
            questions = data.get("clarification_questions", [])

            if score < 75 or not questions:
                return {"clarification_questions": []}

            # Cap at 2 and ensure required fields
            valid = []
            for q in questions[:2]:
                if all(k in q for k in ("id", "dimension", "question", "options")):
                    valid.append(q)

            if valid:
                logger.info(
                    "Ambiguity probe triggered (score=%d) with %d question(s): %s",
                    score,
                    len(valid),
                    [q["dimension"] for q in valid],
                )
            return {"clarification_questions": valid}

        except Exception as exc:
            logger.warning("QuestionAmbiguityNode failed, skipping probe: %s", exc)
            return {"clarification_questions": []}
