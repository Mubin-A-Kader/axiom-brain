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
        
        # Include history context if available
        history = state.get("history_context", "")
        if history and "No prior" not in history:
            try:
                last_q = history.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {search_query}"
            except Exception:
                pass

        summaries = await self._rag.search_table_summaries(tenant_id, source_id, search_query, n_results=10)
        if not summaries:
            return {"selected_tables": []}
            
        summary_text = "\n".join([f"- {s['table']}: {s['summary']}" for s in summaries])
        
        prompt = f"""You are a database routing agent.
Given the user's question, review the following candidate tables and their descriptions.
Select up to 3 tables that are most likely needed to answer the question.

### IMPORTANT:
Table names may be schema-qualified (e.g., "public.users" or "auth.accounts"). 
You MUST return the names EXACTLY as they appear in the list below.

### CANDIDATE TABLES:
{summary_text}

### QUESTION:
{search_query}

Respond ONLY with a JSON list of table names, e.g. ["table1", "table2"]. No other text or markdown."""

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
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

        from axiom.connectors.factory import ConnectorFactory
        dialect_name, dialect_rules = await ConnectorFactory.get_dialect_info(db_type)

        base = f"""You are a precise SQL expert and Enterprise Data Analyst. 
The target database is {dialect_name.upper()}.

### SCHEMA CONTEXT:
{schema_context}

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
3. Use the VERIFIED EXAMPLES as a guide for how this specific tenant structures their queries.
4. If Query Type is NEW_TOPIC, IGNORE the CONVERSATION HISTORY and generate a fresh query for the current Question.
5. If Query Type is REFINEMENT, use the CONVERSATION HISTORY to resolve entities and pronouns, and to understand the base dataset being queried.
   - If the user asks to filter, sort, or select a subset of the previous results (e.g., "in that who is top"), REUSE the SQL from the previous turn and append the necessary ORDER BY, LIMIT, or WHERE clauses to answer the new question.
   - If resolving pronouns or partial names, look for the EXACT literal values (IDs, full names, emails) in the "Result" field of the CONVERSATION HISTORY and use them directly in your SQL.
6. If the user asks for a "date", find the closest column like "created_at" or "timestamp". Do NOT use "order_date" if it is not in the schema.
7. SECURITY MANDATE: You are ONLY allowed to generate `SELECT` queries. NEVER generate `DROP`, `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `ALTER`, or any other destructive commands, even if the user explicitly asks for them. If a user asks to delete or modify data, explain that you are a read-only assistant in <error> tags.
8. Think step-by-step: 
   - Which tables do I need?
   - Do these tables only contain technical IDs? If yes, find the descriptive table to JOIN with.
   - Which columns exist in those tables?
   - How do I join them correctly using the foreign keys shown in SCHEMA CONTEXT?
   - Match exact case for identifiers.
9. Output your thought process inside <thought> tags.
10. Output the final SQL query inside <sql> tags.
11. Return ONLY the tags. No other text. No markdown fences.
12. If you cannot answer the question because the necessary tables/columns do not exist in the schema, output your explanation inside <error> tags and do NOT output any <sql> tags.
13. DIALECT SPECIFIC RULES:
{dialect_rules}

Question: {question}"""
        if critic_feedback:
            base += f"\n\n### CRITIC FEEDBACK (PREVIOUS ATTEMPT FAILED):\n{critic_feedback}\n\nUpdate your query strictly following this technical feedback."
        elif error:
            base += f"\n\n### PREVIOUS ATTEMPT FAILED:\n{error}\n\nReview the SCHEMA CONTEXT carefully. \n- If the error is \"relation ... does not exist\", you likely forgot the schema prefix (e.g. use \"public\".\"tableName\" instead of \"tableName\").\n- If the error suggests a column or table name that exists but with different capitalization, you MUST use double quotes around that name (e.g., \"membershipFees\")."
        return base

    async def __call__(self, state: SQLAgentState) -> dict:
        attempts = state.get("attempts", 0)
        
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

        prompt = await self._build_prompt(state)
        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_temperature,
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
            # Limit to 20 rows for context
            rows = result["rows"][:20]
            return json.dumps(rows, default=str)
        except Exception as exc:
            return f"Investigation execution error: {str(exc)}"

    async def __call__(self, state: SQLAgentState) -> dict:
        question = state["question"]
        sql_query = state.get("sql_query", "")
        error = state.get("error", "")
        schema_context = state.get("schema_context", "")

        if not error:
            return {"critic_feedback": None}

        # If it's a ZERO_RESULTS error, use a specific investigation prompt
        is_zero_results = "ZERO_RESULTS" in error
        
        if is_zero_results:
            prompt = f"""You are an autonomous SQL Data Engineering Agent diagnosing a "0-Result" (Empty Data) failure.
The previous query executed successfully but returned zero rows. This often happens because WHERE or JOIN conditions use incorrect literal values (e.g., wrong casing like 'active' vs 'Active', or typos like 'convrted').

### ORIGINAL QUESTION:
{question}

### SCHEMA CONTEXT:
{schema_context}

### FAILED (ZERO-RESULT) SQL:
{sql_query}

### YOUR CAPABILITIES:
You can investigate the actual data in the database by outputting an investigation query.
To do this, output exactly this format:
INVESTIGATE: <your SQL query here>

For example:
INVESTIGATE: SELECT DISTINCT status FROM orders LIMIT 10

The system will run this query and return the results to you. You can investigate up to 2 times.
Once you have found the correct values, or if you immediately know how to fix the query, output your final technical instructions for the SQL Generator in exactly this format:
FEEDBACK: <your actionable instructions here>"""
        else:
            prompt = f"""You are a Senior Database Administrator. Analyze the failed SQL draft against the execution traceback to identify syntax violations, logical errors, or schema mismatches.
Provide exact, technical correction instructions.

### ORIGINAL QUESTION:
{question}

### SCHEMA CONTEXT:
{schema_context}

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
        
        max_investigations = 2 if is_zero_results else 0
        investigations = 0
        
        while True:
            try:
                response = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=messages,
                    temperature=0.0,
                )
                content = response.choices[0].message.content.strip()
                messages.append({"role": "assistant", "content": content})

                if content.startswith("INVESTIGATE:") and investigations < max_investigations:
                    investigations += 1
                    inv_query = content.replace("INVESTIGATE:", "").strip()
                    logger.info("Critic investigating: %s", inv_query)
                    inv_result = await self._execute_investigation(state, inv_query)
                    logger.info("Critic investigation result: %s", inv_result)
                    messages.append({
                        "role": "user", 
                        "content": f"INVESTIGATION RESULT:\n{inv_result}\n\nIf you need to investigate further, output another INVESTIGATE: query. Otherwise, output your final FEEDBACK: instructions."
                    })
                else:
                    feedback = content.replace("FEEDBACK:", "").strip()
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
                return False, "Query is not a SELECT statement."
            
            # Check for forbidden expressions within the tree (e.g. subqueries that write)
            forbidden = [exp.Update, exp.Delete, exp.Drop, exp.Insert, exp.Create, exp.Alter]
            for node in parsed.find_all(*forbidden):
                return False, f"Forbidden command '{node.key}' detected in SQL."
                
            return True, None
        except Exception as exc:
            logger.warning("SQL parsing failed for security check: %s", exc)
            # If we can't parse it reliably, we block it to be safe
            return False, f"SQL Security Parsing Error: {str(exc)}"

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
            logger.error("Security Violation Blocked: %s (Query: %s)", sec_error, sql)
            return {"sql_result": None, "error": f"Security violation: {sec_error}"}
        
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
                result_update = {
                    "sql_result": None,
                    "error": "ZERO_RESULTS: The query executed successfully but returned 0 rows. Please investigate WHERE/JOIN conditions using your investigation tools."
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
                state["question"],
                state["sql_query"],
                result_update.get("sql_result", ""),
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
        result_json = json.loads(state["sql_result"])
        columns = result_json.get("columns", [])
        rows = result_json.get("rows", [])
        viz_spec = state.get("visualization")

        if not rows:
            return {"response_text": "The query returned no results."}

        # Data sample for synthesis
        sample = rows[:10]
        
        prompt = f"""You are a helpful Data Assistant. 
Based on the user's question and the data results provided, write a concise, professional response.

### USER QUESTION:
{question}

### DATA RESULTS (Columns: {columns}):
{sample}

### TOTAL ROW COUNT: {result_json.get('total_count', len(rows))}

### INSTRUCTIONS:
1. Summarize the answer directly.
2. If there's an obvious trend or significant data point (e.g., "Product X sold the most"), highlight it.
3. If a visualization was generated (Spec: {viz_spec}), mention it (e.g., "I've generated a chart below to show this trend").
4. Keep it under 3 sentences. Do not show raw JSON. 
5. Be precise but conversational.

Response:"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            content = response.choices[0].message.content.strip()
            return {"response_text": content}
        except Exception as exc:
            logger.warning("Failed to synthesize response: %s", exc)
            return {"response_text": "Here are the results for your query:"}
