import json
import logging
import re
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
            # Clean up markdown if any
            import re
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                selected_tables = json.loads(match.group(0))
            else:
                selected_tables = json.loads(content)
                
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
{json.dumps(confirmed_tables) if confirmed_tables else "None. Determine tables autonomously."}
CRITICAL: If a table is listed here, the user explicitly chose it. You MUST prioritize it as the primary table for your query.

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
10. Output your thought process inside <thought> tags.
11. Output the final SQL query inside <sql> tags.
12. Return ONLY the tags. No other text. No markdown fences.
13. If you cannot answer the question because the necessary tables/columns do not exist in the schema, output your explanation inside <error> tags and do NOT output any <sql> tags.
14. DIALECT SPECIFIC RULES:
{dialect_rules}

Question: {question}"""
        if critic_feedback:
            base += f"\n\n### CRITIC FEEDBACK (PREVIOUS ATTEMPT FAILED):\n{critic_feedback}\n\nUpdate your query strictly following this technical feedback."
        elif error:
            base += f"\n\n### PREVIOUS ATTEMPT FAILED:\n{error}\n\nReview the SCHEMA CONTEXT carefully. \n- If the error is \"relation ... does not exist\", you likely forgot the schema prefix (e.g. use \"public\".\"tableName\" instead of \"tableName\").\n- If the error suggests a column or table name that exists but with different capitalization, you MUST use double quotes around that name (e.g., \"membershipFees\")."
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
                "attempts": attempts
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

    async def _execute_investigation(self, state: SQLAgentState, query: str) -> str:
        """Run a read-only investigation query against the database."""
        try:
            from axiom.connectors.factory import ConnectorFactory
            import asyncpg
            source_id = state.get("source_id", "default_source")
            cp_conn = await asyncpg.connect(settings.database_url)
            try:
                row = await cp_conn.fetchrow(
                    "SELECT db_url, db_type, mcp_config FROM data_sources WHERE source_id = $1", 
                    source_id
                )
                if not row: return "Investigation failed: Source not found."
                db_url = row["db_url"]
                db_type = row["db_type"]
                config = json.loads(row["mcp_config"]) if row["mcp_config"] else {}
            finally:
                await cp_conn.close()

            connector = await ConnectorFactory.get_connector(source_id, db_type, db_url, config)
            # Ensure it's a SELECT
            if not query.strip().upper().startswith("SELECT"):
                return "Investigation blocked: Only SELECT queries are allowed."
            
            result = await connector.execute_query(query)
            # Limit to 15 rows for better context
            rows = result["rows"][:15]
            return json.dumps(rows, default=str)
        except Exception as exc:
            return f"Investigation execution error: {str(exc)}"

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
            prompt = f"""You are an autonomous SQL Data Engineering Agent diagnosing a "0-Result" (Empty Data) failure.
The previous query executed successfully but returned zero rows. This often happens because WHERE or JOIN conditions use incorrect literal values (e.g., wrong casing like 'active' vs 'Active', or typos like 'convrted'), or because the JOIN keys simply don't match between tables.

### ORIGINAL QUESTION:
{question}

### SCHEMA CONTEXT:
{schema_context}

### BUSINESS GLOSSARY (SEMANTIC LAYER):
{custom_rules if custom_rules else "None"}

### FAILED (ZERO-RESULT) SQL:
{sql_query}

### YOUR CAPABILITIES:
You can investigate the actual data in the database by outputting an investigation query.
To do this, output exactly this format:
INVESTIGATE: <your SQL query here>

For example:
INVESTIGATE: SELECT * FROM tableA LIMIT 10
or
INVESTIGATE: SELECT DISTINCT status FROM orders LIMIT 20

### INSTRUCTIONS:
1. MANDATORY DATA SAMPLING: Your FIRST action MUST be to sample the actual data from the tables involved in the query. Use `INVESTIGATE: SELECT * FROM <table> LIMIT 10` to see what the data actually looks like.
2. Are the IDs integers or UUID strings? Are the categorical values capitalized? Do the foreign keys in one table's sample actually exist in the other table's sample? DO NOT GUESS.
3. If the investigation shows that the columns you are joining on have completely different data types or values that never match, identify that as the root cause.
4. Output your INVESTIGATE query. The system will run it and give you the results. You can investigate up to 3 times.
5. Only AFTER you have sampled the data and found the correct values or the reason for 0 rows, output your final technical instructions for the SQL Generator in exactly this format:
FEEDBACK: <your actionable instructions here>

If you discover that the data you need is NOT in the current tables, or if a JOIN is impossible because the tables are unrelated, explicitly instruct the SQL Generator to look for a different relationship or state that the current schema context might be missing the required descriptive tables."""
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

### INSTRUCTIONS:
1. Diagnose the root cause of the error based on the traceback.
2. Output explicit, technical correction instructions on how to rewrite the query.
3. Keep the feedback concise and strictly technical.
4. Output your final feedback in this format:
FEEDBACK: <your instructions here>"""

        messages = [{"role": "user", "content": prompt}]
        
        max_investigations = 3 if is_zero_results else 0
        investigations = 0
        
        # Get Dynamic Parameters
        params = AdaptiveInferenceManager.get_parameters("critic", state.get("attempts", 0), error)

        while True:
            try:
                response = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=messages,
                    **params
                )
                content = response.choices[0].message.content.strip()
                messages.append({"role": "assistant", "content": content})

                import re
                
                # Look for INVESTIGATE followed optionally by markdown sql block
                inv_match = re.search(r"INVESTIGATE:\s*```(?:sql)?\s*(.*?)\s*```", content, re.IGNORECASE | re.DOTALL)
                if not inv_match:
                    # Fallback to capturing the rest of the line if no markdown block
                    inv_match = re.search(r"INVESTIGATE:\s*([^\n]+)", content, re.IGNORECASE)
                
                # If the content explicitly contains FEEDBACK: block, prioritize that over investigation if it's the final output
                feedback_match = re.search(r"FEEDBACK:\s*(.*)", content, re.IGNORECASE | re.DOTALL)
                
                if inv_match and not feedback_match and investigations < max_investigations:
                    investigations += 1
                    inv_query = inv_match.group(1).strip()
                    logger.info("Critic investigating: %s", inv_query)
                    inv_result = await self._execute_investigation(state, inv_query)
                    logger.info("Critic investigation result: %s", inv_result)
                    messages.append({
                        "role": "user", 
                        "content": f"INVESTIGATION RESULT:\n{inv_result}\n\nIf you need to investigate further, output another INVESTIGATE: query. Otherwise, output your final FEEDBACK: instructions."
                    })
                else:
                    feedback = content
                    if feedback_match:
                        feedback = feedback_match.group(1).strip()
                    logger.info("Critic Feedback generated.")
                    return {"critic_feedback": feedback}
                    
            except Exception as exc:
                logger.warning("Failed to generate critic feedback: %s", exc)
                return {"critic_feedback": f"Critic analysis failed. Please fix the original error: {error}"}


class SQLExecutionNode:
    def __init__(self, thread_mgr=None) -> None:
        self._thread_mgr = thread_mgr

    async def _is_read_only(self, sql: str, db_type: str = "postgresql") -> tuple[bool, str | None]:
        """Use sqlglot to strictly verify the query is a SELECT statement."""
        try:
            from axiom.connectors.factory import ConnectorFactory
            dialect_name, _ = await ConnectorFactory.get_dialect_info(db_type)
            
            # We assume a single statement for now
            parsed = sqlglot.parse_one(sql, read=dialect_name)
            
            # Check if it's a SELECT-like statement
            if not isinstance(parsed, (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.With)):
                return False, "Security Violation: Query is not a SELECT statement."
            
            # Check for forbidden expressions within the tree (e.g. subqueries that write)
            forbidden = [exp.Update, exp.Delete, exp.Drop, exp.Insert, exp.Create, exp.Alter]
            for node in parsed.find_all(*forbidden):
                return False, f"Security Violation: Forbidden command '{node.key}' detected in SQL."
                
            return True, None
        except Exception as exc:
            logger.warning("SQL parsing failed for security check: %s", exc)
            # If we can't parse it reliably, we block it to be safe, but label it as a syntax error
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
            
            # If we get 0 rows and we still have retries left, trigger the investigator
            if len(all_rows) == 0 and attempts < settings.max_correction_attempts:
                logger.info("0 rows returned. Triggering Zero-Result Investigation Protocol.")
                error_str = "ZERO_RESULTS: The query executed successfully but returned 0 rows. Please investigate WHERE/JOIN conditions using your investigation tools."
                new_error_log = state.get("error_log", []) + [error_str]
                result_update = {
                    "sql_result": None,
                    "error": error_str,
                    "error_log": new_error_log
                }
            else:
                is_truncated = len(all_rows) > 100
                display_rows = all_rows[:100] if is_truncated else all_rows

                # Format to JSON for the LLM/State
                result_update = {
                    "sql_result": json.dumps({
                        "columns": result["columns"], 
                        "rows": display_rows,
                        "is_truncated": is_truncated,
                        "total_count": len(all_rows)
                    }, default=str), 
                    "error": None
                }

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

class DataStorytellingNode:
    """Transform SQL results into a visualization specification for the frontend."""
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        if state.get("error") or not state.get("sql_result"):
            return {"visualization": None}

        question = state["question"]
        result_json = json.loads(state["sql_result"])
        columns = result_json.get("columns", [])
        rows = result_json.get("rows", [])

        if not rows or not columns:
            return {"visualization": None}

        # --- GHOST GRAPH PREVENTION ---
        # 1. Check for insufficient data count
        if len(rows) < 2:
            logger.info("Insufficient rows for visualization. Skipping.")
            return {"visualization": json.dumps({"error_code": "INSUFFICIENT_DATA", "reason": "Single scalar or empty result"})}

        # 2. Check if all numeric values are zero/null
        has_plottable_data = False
        for row in rows:
            for val in row:
                if isinstance(val, (int, float)) and val != 0:
                    has_plottable_data = True
                    break
            if has_plottable_data: break
        
        if not has_plottable_data:
            logger.info("Result set contains only zeros/nulls. Skipping visualization.")
            return {"visualization": json.dumps({"error_code": "INSUFFICIENT_DATA", "reason": "No non-zero plottable values"})}

        # Data sample for the LLM to understand types and values
        sample = rows[:5]

        prompt = f"""You are a Senior Data Analyst. 
Given the user's question and the SQL result set, generate a visualization specification.

### QUESTION:
{question}

### RESULT COLUMNS:
{columns}

### DATA SAMPLE (Top 5 rows):
{sample}

### INSTRUCTIONS:
1. Determine the best plot type: bar, line, scatter, pie, histogram, area, or indicator.
2. Select the correct x_axis and y_axis column names from the RESULT COLUMNS.
3. The title must be a data-driven INSIGHT (e.g., "Revenue grew by 20% in Q4") rather than a generic description.
4. If the data is a single scalar value, use plot_type "indicator".
5. If the data has no obvious trend or categorical breakdown, return null.

### OUTPUT FORMAT (Strict JSON):
{{
  "x_axis": "<column_name | null>",
  "y_axis": "<column_name | list_of_column_names>",
  "plot_type": "<bar | line | scatter | pie | histogram | area | indicator>",
  "title": "<insightful_title>",
  "config": {{
    "show_legend": <true | false>,
    "stack": <true | false>
  }}
}}

Respond ONLY with the JSON object. No markdown, no filler."""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "system", "content": "You are a helpful assistant that only outputs JSON."}, {"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content.strip()

            # Basic validation that it's JSON
            # If the LLM returns "null" or invalid JSON, we catch it
            if content.lower() == "null":
                return {"visualization": None}

            # Validate it's parseable
            json.loads(content) 
            return {"visualization": content}
        except Exception as exc:
            logger.warning("Failed to generate visualization spec: %s", exc)
            return {"visualization": None}


class ResponseSynthesizerNode:
    """Synthesize a human-readable answer/insight based on the SQL result."""
    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        if state.get("error"):
            return {"response_text": f"I encountered an error: {state['error']}"}
        
        if not state.get("sql_result"):
            return {"response_text": "I couldn't find any data to answer your question."}

        question = state["question"]
        
        from axiom.core.cleansing import MLGradeInterceptor
        interceptor = MLGradeInterceptor()
        
        # Apply deterministic ML-grade data cleaning
        cleaned_response = interceptor.process(state["sql_result"], anomaly_method="iqr")
        cleaned_data = cleaned_response.data
        metadata = cleaned_response.metadata
        
        viz_spec = state.get("visualization")

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
8. If there's an obvious trend or anomaly, highlight it. If a visualization was generated (Spec: {viz_spec}), mention it.
9. Keep it concise. Be precise but conversational.

Response:"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            content = response.choices[0].message.content.strip()
            return {"response_text": content, "sql_result": cleaned_response.frontend_json, "layout": "analytics" if viz_spec else "default", "action_bar": cleaned_response.action_bar}
        except Exception as exc:
            logger.warning("Failed to synthesize response: %s", exc)
            return {"response_text": "Here are the results for your query:", "sql_result": cleaned_response.frontend_json, "layout": "analytics" if viz_spec else "default", "action_bar": cleaned_response.action_bar}


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
        
        # 1. Connect to target DB
        import asyncpg
        cp_conn = await asyncpg.connect(settings.database_url, timeout=10)
        try:
            row = await cp_conn.fetchrow(
                "SELECT db_url, db_type FROM data_sources WHERE source_id = $1", 
                source_id
            )
            if not row: return {"critic_feedback": "Discovery failed: Data source not found."}
            target_db_url = row["db_url"]
        finally:
            await cp_conn.close()

        try:
            target_conn = await asyncpg.connect(target_db_url, timeout=15)
        except Exception as conn_err:
            logger.error(f"Discovery connection timeout/failure: {conn_err}")
            return {"critic_feedback": f"Discovery failed: Could not connect to target database within 15s. Verify connectivity to {source_id}.", "error": None}
        
        try:
            # A. Find candidate search terms from the question (entities/names)
            # Use LLM to extract "sniffing targets"
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
            
            # B. Check for "Pivoting" needs if the user rejected the previous table
            pivot_hint = ""
            if negative_constraints:
                pivot_hint = f"The user REJECTED the previous analysis. FAILED PATHS: {negative_constraints}. Performing Neighbor Discovery and Global Grep to avoid these tables..."
            
            # C. Get all searchable columns
            all_cols = await DynamicSchemaMapper.get_searchable_columns(target_conn)
            
            # D. Sniff for values (Force Global Grep if Negative Constraints exist)
            sniff_results = []
            for term in search_terms:
                if len(term) < 3: continue
                hits = await DynamicSchemaMapper.sniff_value(target_conn, term, all_cols)
                sniff_results.extend(hits)

            # E. Synthesize Discovery Feedback
            results_text = "\n".join([f"- Found '{r.sample_value}' in {r.table}.{r.column} (Pattern: {r.pattern_type})" for r in sniff_results])
            
            discovery_feedback = f"""{pivot_hint}
### DISCOVERY RESULTS:
{results_text if results_text else "No hidden data matches found for search terms."}

### ALTERNATIVE PATH STRATEGY:
1. PIVOT: The previous tables were WRONG. You must use the 'Found in' tables listed above instead.
2. EAV Pattern: If data is in a 'key/value' table, map the 'key' column to your intent.
3. Neighbor Search: Use foreign keys to see how these newly discovered tables link to your other entities."""

            return {"critic_feedback": discovery_feedback, "error": None} 

        finally:
            await target_conn.close()
