import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import asyncpg

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

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
