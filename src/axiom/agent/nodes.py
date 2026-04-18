import json
import logging
import re
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


class TableSelectionNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        search_query = state["question"]
        tenant_id = state.get("tenant_id", "default_tenant")
        
        # Include history context if available
        history = state.get("history_context", "")
        if history and "No prior" not in history:
            try:
                last_q = history.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {search_query}"
            except Exception:
                pass

        summaries = await self._rag.search_table_summaries(tenant_id, search_query, n_results=10)
        if not summaries:
            return {"selected_tables": []}
            
        summary_text = "\n".join([f"- {s['table']}: {s['summary']}" for s in summaries])
        
        prompt = f"""You are a database routing agent.
Given the user's question, review the following candidate tables and their descriptions.
Select up to 3 tables that are most likely needed to answer the question.

### CANDIDATE TABLES:
{summary_text}

### QUESTION:
{search_query}

Respond ONLY with a JSON list of table names, e.g. ["table1", "table2"]. No other text or markdown."""

        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        
        try:
            content = response.choices[0].message.content.strip()
            # Clean up markdown if any
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                selected_tables = json.loads(match.group(0))
            else:
                selected_tables = json.loads(content)
        except Exception as exc:
            logger.warning("Failed to parse TableSelectionNode response: %s. Output: %s", exc, content if 'content' in locals() else 'None')
            selected_tables = [s["table"] for s in summaries[:3]] # fallback to top 3
            
        return {"selected_tables": selected_tables}


class SchemaRetrievalNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag

    async def __call__(self, state: SQLAgentState) -> dict:
        tenant_id = state.get("tenant_id", "default_tenant")
        selected_tables = state.get("selected_tables", [])
        search_query = state["question"]
        
        # If there's history, include the last question to help retrieve related examples
        history = state.get("history_context", "")
        if history and "No prior" not in history:
            try:
                # Extract the most recent Q: from history
                last_q = history.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {search_query}"
            except Exception:
                pass

        if selected_tables:
            schema_context = await self._rag.retrieve_exact(tenant_id, selected_tables)
        else:
            # Fallback to vector search if routing yielded nothing
            schema_context = await self._rag.retrieve(tenant_id, search_query)
            
        few_shot_examples = await self._rag.retrieve_examples(tenant_id, search_query)
        
        return {
            "schema_context": schema_context,
            "few_shot_examples": few_shot_examples
        }


class SQLGenerationNode:
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    def _build_prompt(
        self, state: SQLAgentState
    ) -> str:
        schema_context = state["schema_context"]
        question = state["question"]
        error = state.get("error")
        history_context = state.get("history_context", "")
        query_type = state.get("query_type", "NEW_TOPIC")
        custom_rules = state.get("custom_rules", "")
        few_shot_examples = state.get("few_shot_examples", "")

        base = f"""You are a precise SQL expert. 

### SCHEMA CONTEXT:
{schema_context}

### CONVERSATION HISTORY:
{history_context if history_context else "No prior history."}

### TENANT CUSTOM RULES:
{custom_rules if custom_rules else "None"}

### VERIFIED EXAMPLES:
{few_shot_examples if few_shot_examples else "No past examples available."}

### INSTRUCTIONS:
1. Review the SCHEMA CONTEXT carefully. Identify the EXACT table and column names.
2. Adhere strictly to the TENANT CUSTOM RULES if any are provided.
3. Use the VERIFIED EXAMPLES as a guide for how this specific tenant structures their queries.
4. If Query Type is NEW_TOPIC, IGNORE the CONVERSATION HISTORY and generate a fresh query for the current Question.
5. If Query Type is REFINEMENT, use the CONVERSATION HISTORY to resolve pronouns (e.g., "his", "their", "it"). 
   - Look for the literal values (IDs, emails, or keys) in the "Result" field of the CONVERSATION HISTORY.
   - Use these literal values directly in your SQL WHERE clause. Do NOT use placeholders like 'his_id'.
6. If the user asks for a "date", find the closest column like "created_at" or "timestamp". Do NOT use "order_date" if it is not in the schema.
7. Think step-by-step: 
   - Which tables do I need?
   - Which columns exist in those tables?
   - How do I join them?
   - Do any custom rules apply?
8. Output your thought process inside <thought> tags.
9. Output the final SQL query inside <sql> tags.
10. Return ONLY the tags. No other text. No markdown fences.

Question: {question}"""
        if error:
            base += f"\n\n### PREVIOUS ATTEMPT FAILED:\n{error}\n\nReview the SCHEMA CONTEXT and CUSTOM RULES carefully. The column you used probably does not exist. Fix it."
        return base

    async def __call__(self, state: SQLAgentState) -> dict:
        prompt = self._build_prompt(state)
        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_temperature,
        )
        content = response.choices[0].message.content.strip()
        
        # Log thought process for debugging
        thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
        if thought_match:
            logger.info("Agent Thought: %s", thought_match.group(1).strip())

        # Extract SQL from <sql> tags
        sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
        if sql_match:
            sql = sql_match.group(1).strip()
        else:
            # Fallback if tags are missing
            sql = content.replace("```sql", "").replace("```", "").strip()
            
        return {"sql_query": sql, "error": None, "attempts": state["attempts"] + 1}


class SQLExecutionNode:
    def __init__(self, thread_mgr=None) -> None:
        self._thread_mgr = thread_mgr

    async def __call__(self, state: SQLAgentState) -> dict:
        sql = (state["sql_query"] or "").strip()
        if not sql.upper().startswith("SELECT"):
            return {"sql_result": None, "error": "Only SELECT queries are allowed."}
        
        tenant_id = state.get("tenant_id", "default_tenant")
        result_update = {}
        try:
            # 1. Look up tenant DB URL from Control Plane
            cp_conn = await asyncpg.connect(settings.database_url)
            try:
                row = await cp_conn.fetchrow("SELECT db_url FROM tenants WHERE tenant_id = $1", tenant_id)
                if not row or not row["db_url"]:
                    # Fallback to default if no specific tenant found (useful for testing/dev)
                    target_db_url = settings.database_url
                else:
                    target_db_url = row["db_url"]
            finally:
                await cp_conn.close()

            # 2. Execute query on Target DB
            conn = await asyncpg.connect(target_db_url)
            try:
                rows = await conn.fetch(sql)
                if not rows:
                    result_update = {"sql_result": json.dumps({"columns": [], "rows": []}), "error": None}
                else:
                    cols = list(rows[0].keys())
                    data = [list(row.values()) for row in rows]
                    result_update = {"sql_result": _to_json(data, cols), "error": None}
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("SQL execution error: %s", exc)
            result_update = {"sql_result": None, "error": str(exc)}

        if self._thread_mgr and state.get("sql_query") and not result_update.get("error"):
            await self._thread_mgr.save_turn(
                state["thread_id"],
                state["question"],
                state["sql_query"],
                result_update.get("sql_result", ""),
            )
        
        return result_update
