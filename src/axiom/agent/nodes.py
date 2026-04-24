import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

import asyncpg
import sqlglot
from sqlglot import exp

from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG
from axiom.core.inference import AdaptiveInferenceManager

logger = logging.getLogger(__name__)


def _to_json(rows: list, cols: list[str]) -> str:
    def _convert(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return v
    return json.dumps({"columns": cols, "rows": [[_convert(v) for v in row] for row in rows]})


class DatabaseSelectionNode:
    """Intelligently route question to the correct database source."""
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        # If source_id is already provided (explicitly via API), skip routing
        if state.get("source_id"):
            source_id = state["source_id"]
            logger.info("Skipping database routing, source_id already provided: %s", source_id)

            # We still need to know the db_type and custom_rules for the generator
            cp_conn = await asyncpg.connect(settings.database_url)
            try:
                row = await cp_conn.fetchrow(
                    "SELECT db_type, custom_rules FROM data_sources WHERE source_id = $1", 
                    source_id
                )
                db_type = row["db_type"] if row else "postgresql"
                custom_rules = row["custom_rules"] if row and row["custom_rules"] else ""
                return {"source_id": source_id, "db_type": db_type, "custom_rules": custom_rules}
            finally:
                await cp_conn.close()

        tenant_id = state["tenant_id"]
        question = state["question"]

        # 1. Fetch available sources for this tenant
        cp_conn = await asyncpg.connect(settings.database_url)
        try:
            sources = await cp_conn.fetch(
                "SELECT source_id, description, db_type, custom_rules FROM data_sources WHERE tenant_id = $1 AND status = 'active'", 
                tenant_id
            )
        finally:
            await cp_conn.close()

        if not sources:
            logger.warning("No active sources found for tenant %s. Falling back.", tenant_id)
            return {"source_id": "default_tenant", "db_type": "postgresql", "custom_rules": ""} # Fallback

        if len(sources) == 1:
            logger.info("Single source found for tenant, auto-selecting: %s", sources[0]["source_id"])
            return {
                "source_id": sources[0]["source_id"], 
                "db_type": sources[0]["db_type"],
                "custom_rules": sources[0]["custom_rules"] or ""
            }

        # 2. Let the LLM pick the best source based on descriptions
        source_list = "\n".join([f"- {s['source_id']} ({s['db_type']}): {s['description']}" for s in sources])
        prompt = f"""You are a database router.
    A user asked: "{question}"
    Which of the following databases is most likely to contain the answer?

    ### AVAILABLE DATABASES:
    {source_list}

    Respond ONLY with the source_id. No other text."""

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        selected_id = response.choices[0].message.content.strip()

        # Verify it's a valid ID from our list and get its db_type
        selected_source = next((s for s in sources if s["source_id"] == selected_id), sources[0])
        selected_id = selected_source["source_id"]
        db_type = selected_source["db_type"]
        custom_rules = selected_source["custom_rules"] or ""

        logger.info("Routed query to database source: %s (%s)", selected_id, db_type)
        return {"source_id": selected_id, "db_type": db_type, "custom_rules": custom_rules}



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
        source_id = state.get("source_id", "default_source")
        tenant_id = state["tenant_id"]
        
        # --- ENTERPRISE GRADE: Deterministic State Usage ---
        confirmed_tables = state.get("confirmed_tables", [])
        history_tables = state.get("history_tables", [])
        negative_constraints = state.get("negative_constraints", [])
        
        # Include history context if available
        history = state.get("history_context", "")
        if history and "No prior" not in history:
            try:
                last_q = history.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {search_query}"
            except Exception:
                pass

        from axiom.core.discovery import DynamicSchemaMapper
        import asyncpg
        
        # 1. Perform a "Metadata Grep" to find tables by column names
        grep_keywords = [k for k in search_query.lower().split() if len(k) > 3]
        grepped_tables = []
        
        try:
            cp_conn = await asyncpg.connect(settings.database_url, timeout=5)
            try:
                row = await cp_conn.fetchrow("SELECT db_url FROM data_sources WHERE source_id = $1", source_id)
                if row:
                    target_conn = await asyncpg.connect(row["db_url"], timeout=5)
                    try:
                        grepped_tables = await DynamicSchemaMapper.keyword_scan_tables(target_conn, grep_keywords)
                    finally:
                        await target_conn.close()
            finally:
                await cp_conn.close()
        except Exception as e:
            logger.warning(f"Metadata grep failed: {e}")

        # 2. Get RAG Summaries
        summaries = await self._rag.search_table_summaries(tenant_id, source_id, search_query, n_results=20)
        
        # Ensure history_tables are injected into the summary text so the LLM knows about them
        history_summaries_text = ""
        if history_tables:
            # We don't have the RAG summaries for them readily, so we just list them explicitly
            history_summaries_text += "\n### PREVIOUSLY USED TABLES (HIGHLY RELEVANT FOR FOLLOW-UPS):\n" + "\n".join([f"- {t}" for t in history_tables])

        if confirmed_tables:
            history_summaries_text += "\n### USER CONFIRMED PRIMARY SOURCE (MUST BE INCLUDED):\n" + "\n".join([f"- {t}" for t in confirmed_tables])

        # Add negative constraints to the prompt
        if negative_constraints:
            history_summaries_text += "\n### NEGATIVE CONSTRAINTS (DO NOT USE THESE TABLES):\n" + "\n".join([f"- {c}" for c in negative_constraints])

        if not summaries and not grepped_tables and not history_tables and not confirmed_tables:
            return {"selected_tables": []}
            
        summary_text = "\n".join([f"- {s['table']}: {s['summary']}" for s in summaries])
        if grepped_tables:
            summary_text += "\n" + "\n".join([f"- {t}: Potential match found via column name grep." for t in grepped_tables if t not in [s['table'] for s in summaries]])
        
        summary_text += history_summaries_text
        
        prompt = f"""You are a database strategy agent.
Given the user's question, review the following candidate tables.
Your goal is to find ALL possible tables that might contain the answer.

### SEARCH RULE:
If the user confirmed a primary source in the list above, you MUST select it, AND ALSO select any other tables (like 'users' or 'customers') needed to JOIN with it.
If the user's query is a follow-up (using pronouns or relative terms), you MUST select the tables from 'PREVIOUSLY USED TABLES'.
If you see multiple tables that seem to store similar information (e.g. 'shared_user_answers' vs 'template_answers'), you MUST select BOTH. 
Do not try to guess which one is "better" yet—the user will clarify this in the next step.
Include at least 2-3 tables if there is any doubt about the data's location.

### IMPORTANT:
Table names may be schema-qualified. Return names EXACTLY as listed.
CRITICAL: You MUST NOT select any tables listed in the NEGATIVE CONSTRAINTS section. These were explicitly rejected by the user. If you need to find an alternative, look for different tables.

### CANDIDATE TABLES:
{summary_text}

### QUESTION:
{search_query}

Respond ONLY with a JSON list of table names, e.g. ["table1", "table2"]. No other text."""

        # Get Wide Routing Parameters
        params = AdaptiveInferenceManager.get_parameters("routing", 0)

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            **params
        )
        
        try:
            content = response.choices[0].message.content.strip()
            import re
            match = re.search(r"\[.*\]", content, re.DOTALL)
            raw = match.group(0) if match else content
            try:
                selected_tables = json.loads(raw)
            except json.JSONDecodeError:
                # Response was truncated mid-array — extract all quoted strings we can find
                selected_tables = re.findall(r'"([^"]+)"', raw)
                
            # Fallback guarantee: if history_tables exist, force them in case the LLM ignored them
            if history_tables:
                for t in history_tables:
                    # Very basic deduplication, ignoring schema prefix if needed
                    if t not in selected_tables and not any(t in s for s in selected_tables):
                        selected_tables.append(t)
                        
            if confirmed_tables:
                for t in confirmed_tables:
                    if t not in selected_tables and not any(t in s for s in selected_tables):
                        selected_tables.append(t)
                        
        except Exception as exc:
            logger.warning("Failed to parse TableSelectionNode response: %s. Output: %s", exc, content if 'content' in locals() else 'None')
            selected_tables = [s["table"] for s in summaries[:3]] # fallback to top 3
            if history_tables:
                selected_tables.extend(history_tables)
            if confirmed_tables:
                selected_tables.extend(confirmed_tables)
            
        return {"selected_tables": list(set(selected_tables))}


class SchemaRetrievalNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag

    async def __call__(self, state: SQLAgentState) -> dict:
        source_id = state.get("source_id", "default_source")
        tenant_id = state["tenant_id"]
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
            schema_context = await self._rag.retrieve_exact(tenant_id, source_id, selected_tables)
        else:
            # Fallback to vector search if routing yielded nothing
            schema_context = await self._rag.retrieve(tenant_id, source_id, search_query)
            
        few_shot_examples = await self._rag.retrieve_examples(tenant_id, source_id, search_query)
        
        return {
            "schema_context": schema_context,
            "few_shot_examples": few_shot_examples
        }


class SQLGenerationNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def _build_prompt(
        self, state: SQLAgentState
    ) -> str:
        schema_context = state["schema_context"]
        question = state["question"]
        error = state.get("error")
        history_context = state.get("history_context", "")
        query_type = state.get("query_type", "NEW_TOPIC")
        custom_rules = state.get("custom_rules", "")
        few_shot_examples = state.get("few_shot_examples", "")
        db_type = state.get("db_type", "postgresql")
        critic_feedback = state.get("critic_feedback")
        logical_blueprint = state.get("logical_blueprint")
        negative_constraints = state.get("negative_constraints", [])
        confirmed_tables = state.get("confirmed_tables", [])

        from axiom.connectors.factory import ConnectorFactory
        dialect_name, dialect_rules = await ConnectorFactory.get_dialect_info(db_type)

        base = f"""You are a precise SQL expert and Enterprise Data Analyst. 
The target database is {dialect_name.upper()}.

### SCHEMA CONTEXT:
{schema_context}

### USER CONFIRMED TABLES:
{json.dumps(confirmed_tables) if confirmed_tables else "None. You have full autonomy to select ANY tables from the SCHEMA CONTEXT."}
{("CRITICAL: If a table is listed here, the user explicitly chose it. You MUST prioritize it as the primary table for your query. However, you are ALLOWED and ENCOURAGED to use ANY OTHER tables from the SCHEMA CONTEXT to fully answer the question." if confirmed_tables else "")}

### NEGATIVE CONSTRAINTS (PATH BLOCKERS):
{json.dumps(negative_constraints, indent=2) if negative_constraints else "None"}
CRITICAL: If a table or join path is listed in Negative Constraints, it was flagged as WRONG by the user. You are FORBIDDEN from using it. If you cannot answer the question without these tables, use the <error> tags to explain why, rather than repeating a failed path.

### CONVERSATION HISTORY:
{history_context if history_context else "No prior history."}

### BUSINESS GLOSSARY (SEMANTIC LAYER):
{custom_rules if custom_rules else "None"}

### LOGICAL BLUEPRINT:
{logical_blueprint if logical_blueprint else "No blueprint provided. Determine logic directly."}

### VERIFIED EXAMPLES:
{few_shot_examples if few_shot_examples else "No past examples available."}

### INSTRUCTIONS:
1. Review the SCHEMA CONTEXT carefully. Identify the EXACT table and column names.
2. SEMANTIC LAYER ENFORCEMENT: Adhere STRICTLY to the BUSINESS GLOSSARY metrics if any are provided. If the user asks for a metric defined in the glossary (e.g., "Revenue", "Active Users"), you MUST use the EXACT SQL formula provided in the glossary. Do not invent your own calculation.
   - EXCEPTION: If the CRITIC FEEDBACK explicitly instructs you to modify a literal value from the glossary (e.g., changing 'paid' to 'PAID' because of a 0-result failure), you MUST follow the CRITIC'S advice and adjust the literal value to match the actual database.
3. Use the VERIFIED EXAMPLES as a guide for how this specific tenant structures their queries.
4. If Query Type is NEW_TOPIC, IGNORE the CONVERSATION HISTORY and generate a fresh query for the current Question.
5. If Query Type is REFINEMENT, use the CONVERSATION HISTORY to resolve entities and pronouns, and to understand the base dataset being queried.
   - If the user asks to filter, sort, or select a subset of the previous results (e.g., "in that who is top"), REUSE the SQL from the previous turn and append the necessary ORDER BY, LIMIT, or WHERE clauses to answer the new question.
   - If resolving pronouns or partial names, look for the EXACT literal values (IDs, full names, emails) in the "Result" field of the CONVERSATION HISTORY and use them directly in your SQL.
6. If the user asks for a "date", find the closest column like "created_at" or "timestamp". Do NOT use "order_date" if it is not in the schema.
7. SECURITY MANDATE: You are ONLY allowed to generate `SELECT` queries. NEVER generate `DROP`, `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `ALTER`, or any other destructive commands, even if the user explicitly asks for them. If a user asks to delete or modify data, explain that you are a read-only assistant in <error> tags.
8. Think step-by-step: 
   - Which tables do I need?
   - Do these tables only contain technical IDs or UUIDs? If yes, find the descriptive table (e.g., users, products, categories) to JOIN with to get human-readable names.
   - MANDATORY JOIN RULE: Never return a raw UUID (e.g., '2d3f4c9b...') or a technical ID to the user if a descriptive name is available in another table. ALWAYS join to provide the "name", "title", or "label" instead of just the ID.
   - PIVOT RULE: If the previous turns used a table that is now in NEGATIVE CONSTRAINTS, look for neighboring tables using foreign keys or similar names to find the real source of data.
   - Which columns exist in those tables?
   - How do I join them correctly using the foreign keys shown in SCHEMA CONTEXT?
   - Match exact case for identifiers.
9. ZERO-RESULT RECOVERY RULE: If the CRITIC FEEDBACK instructs you to try a completely different JOIN path or to drop a WHERE filter entirely because the previous attempt returned 0 rows, you MUST follow those instructions. Do not stubbornly repeat the exact same JOIN or WHERE clause if it has been proven to fail.
10. NO PLACEHOLDER VALUES — EVER: Never write fake IDs, placeholder UUIDs, or stub values like `'<some_id>'`, `'actual-uuid-here'`, `'<question_id>'`, or similar. If you need a specific ID or lookup value that is not given to you, use a subquery or JOIN to find it from the real lookup table using the EXACT column names shown in SCHEMA CONTEXT. A query with a placeholder is always wrong.
11. DIRECT COLUMN FIRST (STRICT): Before joining through a question/answer table, check the SCHEMA CONTEXT for a column that directly stores the concept (e.g. a profile column named after the attribute). A direct column query is always simpler and less likely to return 0 rows. Only use question/answer tables if no direct column exists.
12. Q&A / EAV TABLE PATTERN (STRICT): When an answers/values table has a `*_id` FK column pointing to a labels/questions/categories table:
   - The label text (question, category name, etc.) lives in the REFERENCED table, not in the answers/values table. Always JOIN to the referenced table and filter the text column there.
   - NEVER filter the value/answer column for the label text. Value columns hold responses ("Yes"/"No"/"Occasionally"), not labels.
   - NEVER write a subquery that searches for a label phrase inside a value/answer column.
   - The correct structure is: JOIN the referenced labels table on the FK column, filter its text column for the label, filter the answers table for the value.
13. OR OPERATOR — ALWAYS PARENTHESISE: AND binds tighter than OR. Without parentheses `a OR b AND c` silently becomes `a OR (b AND c)`. Any WHERE clause mixing OR and AND MUST wrap the OR group: `(cond1 OR cond2) AND cond3`.
14. ILIKE PRECISION — use complete meaningful words: `%yes%` not `%ye%` (matches "they", "money", "player"); `%no%` not `%n%`. Always use the full word or a distinctive substring.
15. Output your thought process inside <thought> tags.
16. Output the final SQL query inside <sql> tags.
17. Return ONLY the tags. No other text. No markdown fences.
15. Use <error> tags ONLY for genuine impossibilities: the required entity truly has no matching table or column anywhere in the schema, OR the request is a destructive command. Do NOT use <error> for vague or broad questions — instead, make a reasonable interpretation and generate SQL. Specifically:
    - "statistics", "summary", "overview", "breakdown", "distribution" → derive sensible aggregates (COUNT, AVG, SUM, GROUP BY) from the available tables.
    - "show me X" where X is ambiguous → pick the most relevant table and return representative data.
    - Uncertainty about which column → use the closest match and note it in <thought>.
    Only refuse if you genuinely cannot find ANY table or column to work with.
16. DIALECT SPECIFIC RULES:
{dialect_rules}

Question: {question}"""
        if critic_feedback:
            base += f"\n\n### CRITIC FEEDBACK (PREVIOUS ATTEMPT FAILED):\n{critic_feedback}\n\nUpdate your query strictly following this technical feedback."
        elif error:
            base += f"\n\n### PREVIOUS ATTEMPT FAILED:\n{error}\n\nReview the SCHEMA CONTEXT carefully. \n- If the error is \"relation ... does not exist\", you likely forgot the schema prefix (e.g. use \"public\".\"tableName\" instead of \"tableName\").\n- If the error suggests a column or table name that exists but with different capitalization, you MUST use double quotes around that name (e.g., \"membershipFees\").\n- If the error is \"function ... does not exist\" and mentions argument types, you MUST explicitly cast the column to numeric or the appropriate type (e.g., `SUM(column::numeric)`, `AVG(CAST(column AS numeric))`)."
        return base

    async def __call__(self, state: SQLAgentState) -> dict:
        attempts = state.get("attempts", 0)
        error = state.get("error")
        
        # 1. Semantic Caching (Skip LLM if semantically identical query exists)
        if not state.get("error") and state.get("query_type") != "REFINEMENT":
            source_id = state.get("source_id", "default_source")
            tenant_id = state["tenant_id"]
            cached = await self._rag.search_semantic_cache(tenant_id, source_id, state["question"])
            if cached:
                logger.info("Semantic cache hit! Distance: %s", cached["distance"])
                return {"sql_query": cached["sql"], "error": None, "attempts": attempts + 1}

        if attempts >= settings.max_correction_attempts:
            return {
                "error": f"Exhausted maximum SQL correction attempts ({settings.max_correction_attempts}). Last error: {state.get('error')}",
                "attempts": 0 # Reset attempts so investigator doesn't instantly die on next query
            }

        # Get Dynamic Parameters
        params = AdaptiveInferenceManager.get_parameters("generation", attempts, error)
        system_msg = AdaptiveInferenceManager.get_system_override("generation")
        
        prompt = await self._build_prompt(state)
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=messages,
            **params
        )
        content = response.choices[0].message.content.strip()
        
        # Log thought process for debugging
        thought = ""
        thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()
            logger.info("Agent Thought: %s", thought)

        # Extract error from <error> tags if present (e.g. semantic impossibility)
        error_match = re.search(r"<error>(.*?)</error>", content, re.DOTALL)
        if error_match:
            return {
                "sql_query": "", 
                "error": error_match.group(1).strip(), 
                "agent_thought": thought,
                "attempts": settings.max_correction_attempts
            }

        # Extract SQL from <sql> tags
        sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
        if sql_match:
            sql = sql_match.group(1).strip()
        else:
            # Fallback if tags are missing
            sql = content.replace("```sql", "").replace("```", "").strip()
            
        return {"sql_query": sql, "error": None, "agent_thought": thought, "attempts": state["attempts"] + 1}


class SQLCriticNode:
    """Analyze failed SQL drafts against execution tracebacks to identify errors and provide feedback."""
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def _get_connector(self, state: SQLAgentState):
        """Return (connector, db_type) for the current source."""
        from axiom.connectors.factory import ConnectorFactory
        source_id = state.get("source_id", "default_source")
        cp_conn = await asyncpg.connect(settings.database_url)
        try:
            row = await cp_conn.fetchrow(
                "SELECT db_url, db_type, mcp_config FROM data_sources WHERE source_id = $1",
                source_id,
            )
            if not row:
                return None, None
            db_url = row["db_url"]
            db_type = row["db_type"]
            config = json.loads(row["mcp_config"]) if row["mcp_config"] else {}
        finally:
            await cp_conn.close()
        connector = await ConnectorFactory.get_connector(source_id, db_type, db_url, config)
        return connector, db_type

    async def _execute_investigation(self, state: SQLAgentState, query: str) -> str:
        """Run a read-only investigation query against the database.

        If a jsonb ILIKE error is detected, automatically rewrites the query
        to cast the offending column to text and retries once.
        """
        try:
            connector, _ = await self._get_connector(state)
            if connector is None:
                return "Investigation failed: Source not found."
            if not query.strip().upper().startswith("SELECT"):
                return "Investigation blocked: Only SELECT queries are allowed."
            try:
                result = await connector.execute_query(query)
                rows = result["rows"][:15]
                return json.dumps(rows, default=str)
            except Exception as exc:
                err = str(exc)

                # jsonb ~~* unknown → auto-cast ILIKE columns and retry
                if "jsonb" in err and ("~~*" in err or "~~" in err or "operator does not exist" in err):
                    fixed = re.sub(
                        r'"([^"]+)"\s+(I?LIKE)',
                        r'"\1"::text \2',
                        query,
                        flags=re.IGNORECASE,
                    )
                    if fixed != query:
                        try:
                            result = await connector.execute_query(fixed)
                            rows = result["rows"][:15]
                            return f"[auto-cast applied]\n{json.dumps(rows, default=str)}"
                        except Exception as exc2:
                            return f"Investigation execution error (after auto-cast): {str(exc2)}"

                # column "x" does not exist → discover real columns and return them
                col_err = re.search(r'column "([^"]+)" does not exist', err, re.IGNORECASE)
                if col_err:
                    tbl_match = re.search(
                        r'FROM\s+"?(\w+)"?\."?(\w+)"?', query, re.IGNORECASE
                    )
                    if tbl_match:
                        schema_n, table_n = tbl_match.group(1), tbl_match.group(2)
                        discovery = (
                            f"SELECT column_name, data_type "
                            f"FROM information_schema.columns "
                            f"WHERE table_schema='{schema_n}' AND table_name='{table_n}' "
                            f"ORDER BY ordinal_position"
                        )
                        try:
                            dcols = await connector.execute_query(discovery)
                            col_list = [f"{r[0]} ({r[1]})" for r in dcols["rows"]]
                            return (
                                f"Column '{col_err.group(1)}' does not exist. "
                                f"Actual columns of {schema_n}.{table_n}: {col_list}. "
                                f"Rewrite your INVESTIGATE query using one of these column names."
                            )
                        except Exception:
                            pass

                return f"Investigation execution error: {err}"
        except Exception as exc:
            return f"Investigation execution error: {str(exc)}"

    async def _auto_probe_zero_results(self, state: SQLAgentState, failed_sql: str) -> str:
        """Run targeted column-value samples on every table touched by the failed SQL.

        Casts all probed columns to ::text so jsonb values appear as their real
        string representation (e.g. '"Non-Vegetarian"') rather than raw JSON objects.
        Also probes FK-neighbor tables so the critic sees alternative paths.
        """
        try:
            connector, _ = await self._get_connector(state)
            if connector is None:
                return ""

            schema_table_re = re.compile(r'"([a-z_]+)"\."([a-zA-Z_0-9]+)"')
            tables = schema_table_re.findall(failed_sql)          # [(schema, table), ...]

            # Columns used in WHERE / ILIKE / = conditions
            filter_col_re = re.compile(
                r'"([^"]+)"(?:::text)?\s+(?:ILIKE|LIKE|=|!=|<>)', re.IGNORECASE
            )
            filter_cols = filter_col_re.findall(failed_sql)

            # Extract (column, pattern) pairs from ILIKE conditions for cross-filter probing
            ilike_pair_re = re.compile(
                r'"([^"]+)"(?:::text)?\s+ILIKE\s+\'([^\']+)\'', re.IGNORECASE
            )
            ilike_pairs = ilike_pair_re.findall(failed_sql)  # [(col, pattern), ...]

            sections: list[str] = []
            seen: set[str] = set()

            async def probe_table(schema: str, table: str) -> None:
                fqt = f'"{schema}"."{table}"'
                if fqt in seen:
                    return
                seen.add(fqt)

                # 5-row sample — cast every value to text so jsonb shows as string
                try:
                    sample = await connector.execute_query(f"SELECT * FROM {fqt} LIMIT 5")
                    sections.append(f"SAMPLE {fqt}:\n{json.dumps(sample['rows'][:5], default=str)}")
                except Exception:
                    pass

                # DISTINCT values of each filter column, always cast to ::text
                for col in filter_cols:
                    try:
                        dist = await connector.execute_query(
                            f'SELECT DISTINCT "{col}"::text FROM {fqt} '
                            f'WHERE "{col}" IS NOT NULL LIMIT 30'
                        )
                        vals = [r[0] for r in dist["rows"]]
                        if vals:
                            sections.append(
                                f'DISTINCT "{col}"::text in {fqt}:\n{json.dumps(vals, default=str)}'
                            )
                    except Exception:
                        pass

                # Cross-filter probe: for Q&A-style tables, sample each ILIKE column
                # filtered by the other ILIKE columns so we see co-occurring values.
                # e.g. sample answerText WHERE questionText ILIKE '%smoke%'
                if len(ilike_pairs) >= 2:
                    for i, (target_col, _) in enumerate(ilike_pairs):
                        where_parts = [
                            f'"{c}"::text ILIKE \'{p}\''
                            for j, (c, p) in enumerate(ilike_pairs)
                            if j != i
                        ]
                        where_clause = " AND ".join(where_parts)
                        try:
                            cross = await connector.execute_query(
                                f'SELECT DISTINCT "{target_col}"::text FROM {fqt} '
                                f'WHERE {where_clause} AND "{target_col}" IS NOT NULL LIMIT 30'
                            )
                            vals = [r[0] for r in cross["rows"]]
                            if vals:
                                sections.append(
                                    f'DISTINCT "{target_col}"::text in {fqt} '
                                    f'(filtered by {where_clause}):\n{json.dumps(vals, default=str)}'
                                )
                        except Exception:
                            pass

                # FK-neighbor probe: discover tables referenced by FKs from this table.
                # Critical for Q&A patterns where question text lives in a sibling table
                # (e.g. ptemplate_answers.question_id → ptemplate_questions).
                try:
                    fk_query = (
                        "SELECT DISTINCT ccu.table_schema, ccu.table_name "
                        "FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        "  ON tc.constraint_name = kcu.constraint_name "
                        "  AND tc.table_schema = kcu.table_schema "
                        "JOIN information_schema.constraint_column_usage ccu "
                        "  ON ccu.constraint_name = tc.constraint_name "
                        "WHERE tc.constraint_type = 'FOREIGN KEY' "
                        f"AND tc.table_schema = '{schema}' "
                        f"AND tc.table_name = '{table}'"
                    )
                    fk_result = await connector.execute_query(fk_query)
                    for fk_row in fk_result["rows"][:4]:
                        fk_schema, fk_table = fk_row[0], fk_row[1]
                        fqt_fk = f'"{fk_schema}"."{fk_table}"'
                        if fqt_fk in seen:
                            continue
                        seen.add(fqt_fk)
                        try:
                            fk_sample = await connector.execute_query(
                                f"SELECT * FROM {fqt_fk} LIMIT 5"
                            )
                            if fk_sample["rows"]:
                                sections.append(
                                    f"FK-NEIGHBOR SAMPLE {fqt_fk} "
                                    f"(referenced by {fqt}):\n"
                                    f"{json.dumps(fk_sample['rows'][:5], default=str)}"
                                )
                        except Exception:
                            pass
                except Exception:
                    pass

            # Probe every table in the failed SQL
            probe_tasks = [probe_table(s, t) for s, t in tables]
            await asyncio.gather(*probe_tasks, return_exceptions=True)

            return "\n\n".join(sections) if sections else ""
        except Exception as exc:
            logger.debug("Auto-probe failed: %s", exc)
            return ""

    async def _fetch_table_catalog(self, state: SQLAgentState) -> str:
        """Return all user-visible tables so the critic can discover sibling tables."""
        try:
            connector, db_type = await self._get_connector(state)
            if connector is None:
                return "Catalog unavailable: source not found."
            # Works for PostgreSQL and MySQL; graceful fallback for others
            if db_type == "mysql":
                catalog_sql = (
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
            else:
                catalog_sql = (
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                    "AND table_type = 'BASE TABLE' ORDER BY table_schema, table_name"
                )
            result = await connector.execute_query(catalog_sql)
            names = [" | ".join(str(v) for v in row) for row in result["rows"]]
            return "\n".join(names) if names else "No tables found."
        except Exception as exc:
            return f"Catalog query error: {str(exc)}"

    async def __call__(self, state: SQLAgentState) -> dict:
        question = state["question"]
        sql_query = state.get("sql_query", "")
        error = state.get("error", "")
        schema_context = state.get("schema_context", "")
        custom_rules = state.get("custom_rules", "")

        if not error:
            return {"critic_feedback": None}

        # If it's a ZERO_RESULTS error, use a specific investigation prompt
        is_zero_results = "ZERO_RESULTS" in error
        
        if is_zero_results:
            # Run both catalog lookup and column-value probe in parallel
            table_catalog, data_probe = await asyncio.gather(
                self._fetch_table_catalog(state),
                self._auto_probe_zero_results(state, sql_query),
                return_exceptions=True,
            )
            if isinstance(table_catalog, Exception):
                table_catalog = "Catalog unavailable."
            if isinstance(data_probe, Exception):
                data_probe = ""

            prompt = f"""You are an autonomous SQL Data Engineering Agent diagnosing a "0-Result" (Empty Data) failure.
The previous query executed successfully but returned zero rows. This can happen because:
- WHERE / JOIN conditions use wrong literal values (wrong casing, typos, numeric vs string IDs)
- The JOIN keys don't match between tables
- **The WRONG TABLE was queried** — a sibling table with a different name may hold the actual data

### ORIGINAL QUESTION:
{question}

### SCHEMA CONTEXT (retrieved at query time):
{schema_context}

### BUSINESS GLOSSARY (SEMANTIC LAYER):
{custom_rules if custom_rules else "None"}

### FAILED (ZERO-RESULT) SQL:
{sql_query}

### ALL TABLES IN THE DATABASE:
{table_catalog}

### PRE-SAMPLED DATA (auto-collected before this prompt):
{data_probe if data_probe else "No samples available."}

### YOUR CAPABILITIES:
Run investigation queries in this exact format:
INVESTIGATE: <SQL here>

CRITICAL RULES FOR YOUR TOOL CALLS:
- NEVER call the same tool with the same arguments twice — results won't change.
- NEVER use ILIKE directly on a jsonb column — cast first: `"col"::text ILIKE '%val%'`
- NEVER include apostrophes in ILIKE patterns (e.g. don't write `ILIKE '%don\'t%'`). Use a shorter keyword without the apostrophe: `ILIKE '%no%'` or `ILIKE '%never%'`.
- When a `describe_table` result shows column names, use ONLY those exact names in subsequent `sample_values` calls. Do not invent column names.
- After sampling, go directly to `run_query` to test alternative SQL. Do not keep sampling — verify quickly.

### INSTRUCTIONS:
1. **Read the PRE-SAMPLED DATA first — before making any tool calls.** It shows DISTINCT values of every filtered column (including cross-filtered values) and FK-NEIGHBOR table samples.

2. **Early-exit if data is conclusively absent (NO tool calls needed):** If the PRE-SAMPLED DATA contains a cross-filter result (labelled "filtered by ...") for the answer/value column, and that result does NOT contain anything resembling the searched value, the data simply does not exist. Do NOT make any tool calls. Immediately output:
FEEDBACK: NO_MATCH — The question exists in the database but no answers match the requested value. Here are the actual stored values for that question: <list the values from the cross-filter sample>

3. **ILIKE match rule:** If the cross-filter sample shows values that are close (different casing, slight wording difference), use the exact stored string and rewrite the SQL. Then output:
VERIFIED_SQL: <corrected SQL>

4. **Wrong-column detection:** If the sampled values of the filtered column contain nothing resembling the filter pattern AND there is no cross-filter result, the filter belongs on a different column or FK-referenced table. Use `describe_table` to confirm, then rewrite.

5. **Multi-path rule:** Only if the PRE-SAMPLED DATA is inconclusive, try up to 3 alternative queries using `run_query`.

6. **Verify then STOP:** The moment `run_query` returns non-empty rows — output:
VERIFIED_SQL: <exact SQL that returned rows>
No further tool calls after this.

7. If ALL paths return 0 rows, output:
FEEDBACK: <what was tried, what values DO exist, and why the requested value wasn't found>"""
        else:
            prompt = f"""You are a Senior Database Administrator. Analyze the failed SQL draft against the execution traceback to identify syntax violations, logical errors, or schema mismatches.
Provide exact, technical correction instructions.

### ORIGINAL QUESTION:
{question}

### SCHEMA CONTEXT:
{schema_context}

### BUSINESS GLOSSARY (SEMANTIC LAYER):
{custom_rules if custom_rules else "None"}

### FAILED SQL DRAFT:
{sql_query}

### EXECUTION TRACEBACK ERROR:
{error}

### YOUR CAPABILITIES:
If you need to look up an actual value (e.g. find the real UUID/ID of a question, category, or lookup row), run:
INVESTIGATE: <SELECT query>
You may investigate once. After investigating, output your final instructions as:
FEEDBACK: <your instructions here>

### INSTRUCTIONS:
1. Diagnose the root cause of the error.
2. If the error is caused by a placeholder value (e.g. '<some_id>', 'actual-uuid-here') — run an INVESTIGATE query to find the real value, then tell the generator to use a JOIN or subquery so no hard-coded IDs are ever needed.
3. If the error is a type/cast issue, instruct the generator to use the correct CAST (e.g. `column::numeric`).
4. Keep feedback concise and strictly technical.
5. Output:
FEEDBACK: <your instructions here>"""

        # ── Tool definitions ──────────────────────────────────────────────────
        # Instead of the fragile "INVESTIGATE: SELECT..." text protocol, the
        # critic LLM calls these structured tools. No regex parsing, no column
        # hallucination, JSONB handled automatically.
        _TOOLS = [
            {
                "type": "function",
                "function": {
                    "name": "describe_table",
                    "description": (
                        "Return the exact column names and data types for a table. "
                        "Use this first whenever you are unsure of a column name."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "schema_name": {"type": "string", "description": "e.g. 'public'"},
                            "table_name":  {"type": "string", "description": "e.g. 'ptemplate_questions'"},
                        },
                        "required": ["schema_name", "table_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sample_values",
                    "description": (
                        "Return up to 30 DISTINCT values of a column cast to text. "
                        "Use this to see the real stored strings (casing, hyphens, JSON format) "
                        "before writing an ILIKE pattern."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "schema_name": {"type": "string"},
                            "table_name":  {"type": "string"},
                            "column_name": {"type": "string"},
                        },
                        "required": ["schema_name", "table_name", "column_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_query",
                    "description": (
                        "Execute a read-only SELECT query and return up to 15 rows. "
                        "CRITICAL: If the result is non-empty (has rows), you MUST immediately "
                        "stop all further tool calls and output exactly:\n"
                        "VERIFIED_SQL: <that exact SQL query>\n"
                        "Do NOT make any more tool calls after finding a working query."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql": {"type": "string"},
                        },
                        "required": ["sql"],
                    },
                },
            },
        ]

        async def _dispatch_tool(name: str, args: dict) -> str:
            """Execute whichever tool the LLM called and return a string result."""
            try:
                connector, _ = await self._get_connector(state)
                if connector is None:
                    return "Tool error: source not found."

                if name == "describe_table":
                    sql = (
                        "SELECT column_name, data_type "
                        "FROM information_schema.columns "
                        f"WHERE table_schema='{args['schema_name']}' "
                        f"AND table_name='{args['table_name']}' "
                        "ORDER BY ordinal_position"
                    )
                    r = await connector.execute_query(sql)
                    cols = [f"{row[0]} ({row[1]})" for row in r["rows"]]
                    return json.dumps(cols)

                if name == "sample_values":
                    fqt = f"\"{args['schema_name']}\".\"{args['table_name']}\""
                    col = args["column_name"]
                    sql = (
                        f"SELECT DISTINCT \"{col}\"::text "
                        f"FROM {fqt} "
                        f"WHERE \"{col}\" IS NOT NULL LIMIT 30"
                    )
                    r = await connector.execute_query(sql)
                    return json.dumps([row[0] for row in r["rows"]], default=str)

                if name == "run_query":
                    raw = args["sql"].strip()
                    if not raw.upper().startswith("SELECT"):
                        return "Blocked: only SELECT queries allowed."
                    # Auto-fix jsonb ILIKE on the fly
                    fixed = re.sub(
                        r'"([^"]+)"\s+(I?LIKE)',
                        r'"\1"::text \2',
                        raw, flags=re.IGNORECASE,
                    )
                    r = await connector.execute_query(fixed)
                    return json.dumps(r["rows"][:15], default=str)

                return f"Unknown tool: {name}"
            except Exception as exc:
                return f"Tool error ({name}): {exc}"

        # ── LLM loop with tool use ────────────────────────────────────────────
        messages = [{"role": "user", "content": prompt}]
        params = AdaptiveInferenceManager.get_parameters("critic", state.get("attempts", 0), error)
        max_tool_calls = 8 if is_zero_results else 3
        tool_calls_made = 0

        while True:
            try:
                response = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=messages,
                    tools=_TOOLS,
                    tool_choice="auto",
                    **{k: v for k, v in params.items() if k not in ("stream",)},
                )
                msg = response.choices[0].message
                finish = response.choices[0].finish_reason

                # ── Tool call branch ──────────────────────────────────────────
                if finish == "tool_calls" and msg.tool_calls and tool_calls_made < max_tool_calls:
                    messages.append(msg)          # append assistant message with tool_calls
                    for tc in msg.tool_calls:
                        tool_calls_made += 1
                        try:
                            args = json.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        logger.info("Critic tool call: %s(%s)", tc.function.name, args)
                        result = await _dispatch_tool(tc.function.name, args)
                        logger.info("Critic tool result: %s", result[:200])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                    continue   # send tool results back to the model

                # ── Final text response ──────────────────────────────────────
                content = (msg.content or "").strip()

                # VERIFIED_SQL: critic already confirmed this query returns rows
                # → skip generate_sql entirely, inject directly as the next query
                verified_match = re.search(
                    r"VERIFIED_SQL:\s*```(?:sql)?\s*(.*?)\s*```|VERIFIED_SQL:\s*(SELECT\s.+)",
                    content, re.IGNORECASE | re.DOTALL,
                )
                if verified_match:
                    verified_sql = (verified_match.group(1) or verified_match.group(2) or "").strip()
                    if verified_sql:
                        logger.info("Critic found verified working SQL — bypassing generator.")
                        return {
                            "critic_feedback": None,
                            "sql_query": verified_sql,
                            "error": None,
                        }

                feedback_match = re.search(r"FEEDBACK:\s*(.*)", content, re.IGNORECASE | re.DOTALL)
                feedback = feedback_match.group(1).strip() if feedback_match else content
                logger.info("Critic Feedback generated.")
                return {"critic_feedback": feedback}

            except Exception as exc:
                logger.warning("Failed to generate critic feedback: %s", exc)
                return {"critic_feedback": f"Critic analysis failed. Please fix the original error: {error}"}


class SQLExecutionNode:
    def __init__(self, thread_mgr=None, rag=None) -> None:
        self._thread_mgr = thread_mgr
        self._rag = rag

    async def _is_read_only(self, sql: str, db_type: str = "postgresql") -> tuple[bool, str | None]:
        """Use sqlglot to strictly verify the query is a SELECT statement."""
        try:
            from axiom.connectors.factory import ConnectorFactory
            dialect_name, _ = await ConnectorFactory.get_dialect_info(db_type)

            # parse() returns a list — handles multi-statement SQL that parse_one
            # would return as exp.Block, which is not in the SELECT-like types.
            statements = sqlglot.parse(sql, read=dialect_name)
            if not statements:
                return False, "Security Violation: Empty query."

            allowed = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.With)
            forbidden = (exp.Update, exp.Delete, exp.Drop, exp.Insert, exp.Create, exp.Alter)

            for stmt in statements:
                if stmt is None:
                    continue
                if not isinstance(stmt, allowed):
                    return False, "Security Violation: Query is not a SELECT statement."
                for node in stmt.find_all(*forbidden):
                    return False, f"Security Violation: Forbidden command '{node.key}' detected in SQL."

            return True, None
        except Exception as exc:
            logger.warning("SQL parsing failed for security check: %s", exc)
            # sqlglot can fail on valid SQL with unusual escaping (e.g. don\'t).
            # Fall back to a simple string check rather than blocking the whole query.
            sql_upper = sql.strip().upper()
            write_keywords = ("DROP ", "DELETE ", "UPDATE ", "INSERT ", "TRUNCATE ", "ALTER ", "CREATE ")
            if sql_upper.startswith("SELECT") and not any(kw in sql_upper for kw in write_keywords):
                logger.info("SQL parsing fallback: allowing query that starts with SELECT and has no write keywords.")
                return True, None
            return False, f"SQL Syntax Error (Blocked for Security): {str(exc)}"

    async def __call__(self, state: SQLAgentState) -> dict:
        if state.get("error"):
            # If an error was intentionally set (like missing tables), bypass execution.
            return {"sql_result": None, "error": state.get("error")}

        sql = (state["sql_query"] or "").strip()
        if not sql:
             return {"sql_result": None, "error": "No SQL query generated."}
        
        # 1. Robust Security Validation
        db_type = state.get("db_type", "postgresql")
        safe, sec_error = await self._is_read_only(sql, db_type)
        if not safe:
            # If it's a syntax error, we don't use the scary "Security violation" prefix in the logger
            if "Syntax Error" in sec_error:
                logger.info("SQL Validation failed: %s (Query: %s)", sec_error, sql)
            else:
                logger.error("Security Violation Blocked: %s (Query: %s)", sec_error, sql)
            return {"sql_result": None, "error": sec_error}
        
        source_id = state.get("source_id", "default_source")
        result_update = {}
        try:
            # 1. Look up source DB details from Control Plane
            from axiom.connectors.factory import ConnectorFactory
            
            cp_conn = await asyncpg.connect(settings.database_url)
            try:
                row = await cp_conn.fetchrow(
                    "SELECT db_url, db_type, mcp_config FROM data_sources WHERE source_id = $1", 
                    source_id
                )
                if not row:
                    # Fallback to default if no specific source found
                    target_db_url = settings.database_url
                    db_type = "postgresql"
                    config = {}
                else:
                    target_db_url = row["db_url"]
                    db_type = row["db_type"]
                    config = json.loads(row["mcp_config"]) if row["mcp_config"] else {}
            finally:
                await cp_conn.close()

            # 2. Execute query via the appropriate Connector (handles pooling automatically)
            connector = await ConnectorFactory.get_connector(source_id, db_type, target_db_url, config)
            result = await connector.execute_query(sql)
            
            # --- RESPONSE LIMITING & ZERO-RESULT PROTOCOL ---
            all_rows = result["rows"]
            attempts = state.get("attempts", 0)

            # Zero rows → route to critic's INVESTIGATE loop instead of treating as success
            if not all_rows:
                result_update = {
                    "sql_result": None,
                    "error": f"ZERO_RESULTS: The query executed successfully but returned 0 rows. SQL: {state.get('sql_query', '')}",
                }
            else:
                is_truncated = len(all_rows) > 100
                display_rows = all_rows[:100] if is_truncated else all_rows
                result_update = {
                    "sql_result": json.dumps({
                        "columns": result["columns"],
                        "rows": display_rows,
                        "is_truncated": is_truncated,
                        "total_count": len(all_rows)
                    }, default=str),
                    "error": None
                }

                # Auto-save every successful query as a few-shot example so future
                # queries on the same tenant/source learn from real working SQL.
                if self._rag and state.get("question") and sql:
                    try:
                        await self._rag.ingest_example(
                            state.get("tenant_id", "default"),
                            state.get("source_id", "default_source"),
                            state["question"],
                            sql,
                        )
                    except Exception as ex:
                        logger.debug("Failed to save example to RAG: %s", ex)

        except Exception as exc:
            logger.warning("SQL execution error: %s", exc)
            result_update = {"sql_result": None, "error": str(exc)}

        if self._thread_mgr and state.get("sql_query") and not result_update.get("error"):
            await self._thread_mgr.save_turn(
                state["thread_id"],
                state.get("tenant_id", "default"),
                state["question"],
                state["sql_query"],
                result_update.get("sql_result", ""),
                active_filters=state.get("active_filters", []),
                verified_joins=state.get("verified_joins", []),
                error_log=state.get("error_log", []),
                llm_model=state.get("llm_model"),
                source_id=state.get("source_id"),
            )
        
        return result_update

class HumanApprovalNode:
    """A pass-through node used purely to trigger LangGraph's interrupt_before."""
    async def __call__(self, state: SQLAgentState) -> dict:
        # No state modification needed. The pause happens BEFORE this node executes.
        return {}

class NotebookArtifactNode:
    """Transform SQL results into an executed notebook artifact."""
    def __init__(self) -> None:
        from axiom.notebooks.artifacts import NotebookArtifactStore
        from axiom.notebooks.executor_client import NotebookExecutorClient

        self._store = NotebookArtifactStore(settings.artifact_root)
        self._executor = NotebookExecutorClient(
            settings.notebook_executor_url,
            settings.notebook_execution_timeout,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        # action_plan clears sql_result to None after RCA; fall back to last_sql_result
        raw_result = state.get("sql_result") or state.get("last_sql_result")
        if state.get("error") or not raw_result or raw_result == "CONCLUDED":
            return {"artifact": None}

        from axiom.notebooks.builder import build_analysis_notebook

        artifact_id = str(uuid.uuid4())
        tenant_id = state.get("tenant_id", "default_tenant")
        thread_id = state.get("thread_id", artifact_id)

        try:
            result_json = json.loads(raw_result)
            columns = result_json.get("columns", [])
            rows = result_json.get("rows", [])
            if not isinstance(columns, list) or not isinstance(rows, list):
                raise ValueError("SQL result must contain list-valued columns and rows")

            notebook, cells_summary = build_analysis_notebook(
                question=state["question"],
                sql=state.get("sql_query") or "",
                result=result_json,
                insight=state.get("response_text"),
            )

            execution = await self._executor.execute(
                tenant_id=tenant_id,
                thread_id=thread_id,
                artifact_id=artifact_id,
                notebook=notebook,
            )
            artifact = self._store.save(
                artifact_id=artifact_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
                notebook=execution.get("notebook") or notebook,
                status=execution.get("status", "failed"),
                outputs=execution.get("outputs", []),
                cells_summary=cells_summary,
                execution_error=execution.get("execution_error"),
                logs=execution.get("logs"),
            )
            return {"artifact": artifact}
        except Exception as exc:
            logger.warning("Failed to build notebook artifact: %s", exc)
            try:
                fallback_result = json.loads(raw_result)
            except Exception:
                fallback_result = {"columns": ["error"], "rows": [[str(exc)]]}
            fallback_notebook, cells_summary = build_analysis_notebook(
                question=state["question"],
                sql=state.get("sql_query") or "",
                result=fallback_result,
                insight=state.get("response_text"),
            )
            artifact = self._store.save(
                artifact_id=artifact_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
                notebook=fallback_notebook,
                status="failed",
                cells_summary=cells_summary,
                execution_error=str(exc),
            )
            return {"artifact": artifact}


class ResponseSynthesizerNode:
    """Synthesize a human-readable answer/insight based on the SQL result."""
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    @staticmethod
    def _extract_zero_result_signal(feedback: str) -> tuple[bool, str]:
        """Pull the NO_MATCH flag and available-values list out of critic feedback.

        Returns (no_match: bool, clean_summary: str).  Deliberately strips raw
        probe rows / sample dumps so the synthesis LLM only sees intent-level
        information, not raw DB data that would cause hallucination.
        """
        no_match = "NO_MATCH" in feedback
        # Find an explicit list of available values mentioned after common lead-in phrases
        values_match = re.search(
            r'(?:available|stored|actual|existing)\s+(?:values?|answers?|options?)[:\s]+([^\n]{3,200})',
            feedback, re.IGNORECASE,
        )
        available_str = values_match.group(1).strip().rstrip(".") if values_match else ""
        if available_str:
            return no_match, f"Available values: {available_str}"
        # Fall back: use first sentence only (avoid embedding multi-line probe dumps)
        first_sentence = re.split(r'[\n.]{1}', feedback.replace("NO_MATCH — ", ""))[0].strip()
        return no_match, first_sentence[:300]

    async def __call__(self, state: SQLAgentState) -> dict:
        if state.get("error"):
            error = state["error"]
            # For zero-result failures, surface only the clean investigation signal —
            # never the raw probe data which causes hallucination.
            if "ZERO_RESULTS" in error:
                feedback = state.get("critic_feedback") or ""
                question = state.get("question", "your question")
                no_match, clean_signal = self._extract_zero_result_signal(feedback)
                try:
                    response = await self._client.chat.completions.create(
                        model=state.get("llm_model") or settings.llm_model,
                        messages=[{"role": "user", "content": (
                            f"The user asked: \"{question}\"\n\n"
                            f"A database search found no matching records. "
                            f"Investigation note: {clean_signal}\n\n"
                            "Write exactly 1-2 sentences for the user. "
                            "State clearly that no results were found. "
                            "If 'Available values' are listed above, mention them as the options that DO exist. "
                            "Do NOT mention SQL, tables, databases, raw data, photos, or any technical details. "
                            "Do NOT invent information not stated in the investigation note above."
                        )}],
                        temperature=0.2,
                    )
                    return {"response_text": response.choices[0].message.content.strip()}
                except Exception:
                    msg = f"No results found for your query."
                    if clean_signal:
                        msg += f" {clean_signal}."
                    return {"response_text": msg}
            return {"response_text": f"I encountered an error: {error}"}

        # RCA path: ActionPlanNode already populated response_text and cleared sql_result
        if state.get("response_text") and not state.get("sql_result"):
            artifact = state.get("artifact")
            return {
                "response_text": state["response_text"],
                "layout": "notebook" if artifact else "default",
                "action_bar": [],
            }

        if not state.get("sql_result"):
            return {"response_text": "I couldn't find any data to answer your question."}

        question = state["question"]
        
        from axiom.core.cleansing import MLGradeInterceptor
        interceptor = MLGradeInterceptor()
        
        # Apply deterministic ML-grade data cleaning
        cleaned_response = interceptor.process(state["sql_result"], anomaly_method="iqr")
        cleaned_data = cleaned_response.data
        metadata = cleaned_response.metadata
        
        artifact = state.get("artifact")

        if not cleaned_data:
            return {"response_text": "The query returned no results."}

        # Extract column names from the cleaned data
        columns = list(cleaned_data[0].keys()) if cleaned_data else []

        # Data sample for synthesis
        sample = cleaned_data[:5]
        
        prompt = f"""You are a senior Business Analyst. 
Based on the user's question and the cleaned data results provided, write a concise, professional response.

### USER QUESTION:
{question}

### DATA RESULTS (Columns: {columns}):
{sample}

### METADATA:
- Original Row Count: {metadata.row_count_original}
- Cleaned Row Count: {metadata.row_count_cleaned}
- Anomalies Detected: {metadata.anomaly_detected}
- Summary Stats: {json.dumps(metadata.summary_stats, indent=2)}

### INSTRUCTIONS:
1. ABSOLUTELY NO RAW DATA TABLES OR JSON. You must ONLY output a conversational summary. NEVER render the data as a table, markdown grid, or raw list unless explicitly asked by the user to "show me a table".
2. NEVER output raw UUIDs, internal IDs (e.g., user_id, file_key), or technical column names.
3. NEVER print "null" or "None". Instead say "missing" or "not provided".
4. DO NOT show database field names like `collection_id`, `status`, `created_at`. Use plain labels: "collection", "status", "upload date".
5. If you need to reference an image or record, use its human-readable title or a short description. Never show the full UUID.
6. Aggregate counts and list only the most relevant examples (max 5 items).
7. Output in plain English, with bullet points or short sentences. 
8. If there's an obvious trend or anomaly, highlight it. If an analysis notebook artifact was generated, mention that the workspace contains the executable notebook.
9. Keep it concise. Be precise but conversational.

Response:"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            content = response.choices[0].message.content.strip()
            return {"response_text": content, "sql_result": cleaned_response.frontend_json, "layout": "notebook" if artifact else "default", "action_bar": cleaned_response.action_bar}
        except Exception as exc:
            logger.warning("Failed to synthesize response: %s", exc)
            return {"response_text": "Here are the results for your query:", "sql_result": cleaned_response.frontend_json, "layout": "notebook" if artifact else "default", "action_bar": cleaned_response.action_bar}


class DiscoveryNode:
    """
    Detective Mode: Triggered on failure or 0-results to sniff for dynamic data patterns.
    """
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        error = state.get("error", "")
        sql_query = state.get("sql_query", "")
        source_id = state.get("source_id", "default_source")
        question = state["question"]
        negative_constraints = state.get("negative_constraints", [])
        
        logger.info("DiscoveryNode triggered: Sniffing for hidden data patterns...")

        from axiom.core.discovery import DynamicSchemaMapper
        import asyncpg

        # 1. Look up db_type and config from control plane
        cp_conn = await asyncpg.connect(settings.database_url, timeout=10)
        try:
            row = await cp_conn.fetchrow(
                "SELECT db_url, db_type, mcp_config FROM data_sources WHERE source_id = $1",
                source_id
            )
            if not row:
                return {"critic_feedback": "Discovery failed: Data source not found."}
            db_url = row["db_url"]
            db_type = row["db_type"]
            config = json.loads(row["mcp_config"]) if row["mcp_config"] else {}
        finally:
            await cp_conn.close()

        # 2. Reuse the existing connector (SSH tunnel already running) rather than raw asyncpg
        try:
            from axiom.connectors.factory import ConnectorFactory
            connector = await ConnectorFactory.get_connector(source_id, db_type, db_url, config)
            if not connector._pool:
                await connector.connect()
            target_conn = await connector._pool.acquire()
        except Exception as conn_err:
            logger.error(f"Discovery connection timeout/failure: {conn_err}")
            return {"critic_feedback": f"Discovery failed: Could not connect to target database. Verify connectivity to {source_id}.", "error": None}

        try:
            # A. If the error is a "relation does not exist", find the actual table name first
            similar_table_hint = ""
            import re as _re
            does_not_exist_match = _re.search(r'relation "?([^"]+)"? does not exist', error, _re.IGNORECASE)
            if does_not_exist_match:
                bad_table = does_not_exist_match.group(1)
                similar = await DynamicSchemaMapper.find_similar_tables(target_conn, bad_table)
                if similar:
                    similar_table_hint = (
                        f"\n### TABLE NAME FIX:\n"
                        f"The table '{bad_table}' does NOT exist. "
                        f"The following real tables in the database have similar names — use one of these instead:\n"
                        + "\n".join(f"  - public.\"{t}\"" for t in similar)
                        + "\nUse double-quotes around the table name to preserve exact case.\n"
                    )
                    logger.info("DiscoveryNode table name fix: %s → %s", bad_table, similar)

            # B. Find candidate search terms from the question (entities/names)
            extract_prompt = f"""Extract the primary search subjects (entities, partial names, or attribute values) from this question: "{question}"
            Return ONLY a comma-separated list of terms. No other text."""

            # Get Dynamic Parameters for Discovery
            discovery_params = AdaptiveInferenceManager.get_parameters("discovery", state.get("attempts", 0), error)
            discovery_system = AdaptiveInferenceManager.get_system_override("discovery")

            res = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": discovery_system},
                    {"role": "user", "content": extract_prompt}
                ],
                **discovery_params
            )
            search_terms = [t.strip() for t in res.choices[0].message.content.split(",")]

            # C. Check for "Pivoting" needs if the user rejected the previous table
            pivot_hint = ""
            if negative_constraints:
                pivot_hint = f"The user REJECTED the previous analysis. FAILED PATHS: {negative_constraints}. Performing Neighbor Discovery and Global Grep to avoid these tables..."

            # D. Get all searchable columns and sniff for values
            all_cols = await DynamicSchemaMapper.get_searchable_columns(target_conn)

            sniff_results = []
            for term in search_terms:
                if len(term) < 3: continue
                hits = await DynamicSchemaMapper.sniff_value(target_conn, term, all_cols)
                sniff_results.extend(hits)

            # E. Synthesize Discovery Feedback
            results_text = "\n".join([f"- Found '{r.sample_value}' in {r.table}.{r.column} (Pattern: {r.pattern_type})" for r in sniff_results])

            discovery_feedback = f"""{similar_table_hint}{pivot_hint}
### DISCOVERY RESULTS:
{results_text if results_text else "No hidden data matches found for search terms."}

### ALTERNATIVE PATH STRATEGY:
1. PIVOT: The previous tables were WRONG. You must use the 'Found in' tables listed above instead.
2. EAV Pattern: If data is in a 'key/value' table, map the 'key' column to your intent.
3. Neighbor Search: Use foreign keys to see how these newly discovered tables link to your other entities."""

            return {"critic_feedback": discovery_feedback, "error": None}

        finally:
            await connector._pool.release(target_conn)
