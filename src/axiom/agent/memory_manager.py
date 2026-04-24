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
        thread_id = state["thread_id"]
        active_filters = state.get("active_filters", [])
        verified_joins = state.get("verified_joins", [])
        error_log = state.get("error_log", [])
        history_context = state.get("history_context", "")
        confirmed_tables = state.get("confirmed_tables", [])

        # 1. Fetch Negative Constraints and Confirmed Tables from Corrective Memory Grafting
        from axiom.agent.thread import ThreadManager
        thread_mgr = ThreadManager()
        metadata = await thread_mgr.get_thread_metadata(thread_id)
        negative_constraints = metadata.get("negative_constraints", [])
        
        # Load previously confirmed tables for this thread so follow-up queries don't lose context
        if not confirmed_tables:
            confirmed_tables = metadata.get("confirmed_tables", [])

        # Extract tables from the previous successful SQL for "Refinement" context
        history_tables = []
        if history_context and "No prior" not in history_context:
            import re
            sql_matches = re.findall(r"SQL:\s*(.*?)\nResult:", history_context, re.DOTALL)
            if sql_matches:
                last_sql = sql_matches[-1].strip()
                try:
                    from sqlglot import parse_one, exp
                    parsed = parse_one(last_sql)
                    for table in parsed.find_all(exp.Table):
                        # Ensure we get the raw table name without schema/quotes for matching
                        history_tables.append(table.name)
                except Exception as e:
                    logger.warning(f"Failed to parse history SQL for tables: {e}")

        # --- ENTERPRISE GRADE: Deterministic Command Routing ---
        # If the frontend sends a system command (like a card click), we process it
        # deterministically and BYPASS the LLM entirely to guarantee 100% reliability
        # and save tokens/latency.
        if question.startswith("CONFIRMED_SOURCE:"):
            import re
            table_match = re.search(r"Use the '(.*?)' table", question)
            q_match = re.search(r"answer my question about '(.*?)'", question)
            
            if table_match:
                confirmed_table = table_match.group(1)
                real_question = q_match.group(1) if q_match else question
                logger.info(f"System Command Executed: Confirmed Source -> {confirmed_table}")
                
                # Persist this confirmed table to the thread metadata for future turns
                metadata["confirmed_tables"] = [confirmed_table]
                client = await thread_mgr._get_client()
                key = f"axiom:thread:{thread_id}"
                data = await client.get(key)
                if data:
                    parsed_data = json.loads(data)
                    parsed_data["metadata"] = metadata
                    await client.setex(key, 86400, json.dumps(parsed_data))
                
                return {
                    "question": real_question, # Strip the system command so the SQL generator just sees the intent
                    "active_filters": active_filters,
                    "verified_joins": verified_joins,
                    "error_log": error_log,
                    "negative_constraints": negative_constraints,
                    "confirmed_tables": [confirmed_table],
                    "history_tables": history_tables
                }

        if question.startswith("REJECTED_INTENT:"):
            import re
            table_match = re.search(r"The suggested tables \[(.*?)\]", question)
            q_match = re.search(r"answer my question about '(.*?)'", question)
            
            real_question = q_match.group(1) if q_match else question
            
            if table_match:
                rejected_tables_str = table_match.group(1)
                # Parse the list of strings (simple split or ast)
                rejected_tables = [t.strip().strip("'\"") for t in rejected_tables_str.split(",")]
                
                logger.info(f"System Command Executed: Rejected Intent -> {rejected_tables}")
                
                # Persist this to the thread metadata so the next turn's TableSelectionNode picks it up
                constraint = f"FAIL_PATH: The user explicitly rejected the following tables for the intent '{real_question}': {rejected_tables}. DO NOT USE THESE TABLES."
                negative_constraints.append(constraint)
                metadata["negative_constraints"] = negative_constraints
                
                client = await thread_mgr._get_client()
                key = f"axiom:thread:{thread_id}"
                data = await client.get(key)
                if data:
                    parsed_data = json.loads(data)
                    parsed_data["metadata"] = metadata
                    await client.setex(key, 86400, json.dumps(parsed_data))
                
                return {
                    "question": real_question, 
                    "active_filters": active_filters,
                    "verified_joins": verified_joins,
                    "error_log": error_log,
                    "negative_constraints": negative_constraints,
                    "confirmed_tables": confirmed_tables,
                    "history_tables": history_tables
                }

        # --- Standard Natural Language Processing ---
        prompt = f"""You are the Memory Manager of a State-Aware Text-to-SQL Orchestrator.
Your job is to update the structured semantic memory based on the latest user question and the prior state.

### CURRENT STATE:
Active Filters: {json.dumps(active_filters)}
Verified Joins: {json.dumps(verified_joins)}

### NEGATIVE CONSTRAINTS (PREVIOUSLY FAILED PATHS - DO NOT REPEAT):
{json.dumps(negative_constraints, indent=2) if negative_constraints else "None"}

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
5. NEGATIVE ENFORCEMENT: If there are Negative Constraints, ensure your updated joins and filters DO NOT reuse the tables or logic flagged as WRONG.
6. Express filters clearly, e.g., "region = 'Europe'" or "Year=2023".
7. Express verified joins clearly if mentioned or implied by recent successful queries, e.g., "users JOIN orders".

Respond strictly with valid JSON in this format:
{{
  "query_type": "NEW_TOPIC" or "REFINEMENT",
  "active_filters": ["filter1", "filter2"],
  "verified_joins": ["join1"]
}}

query_type rules:
- "REFINEMENT" if the new question refers to, continues, or adds detail to the previous result (e.g. "show detailed statistics", "filter that by X", "how many of those", "in that list who is...", "break it down by...")
- "NEW_TOPIC" if it's a completely different question with no relationship to prior turns
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
            query_type = result.get("query_type", "NEW_TOPIC")

        except Exception as exc:
            logger.warning("Failed to parse memory manager response: %s", exc)
            new_filters = active_filters
            new_joins = verified_joins
            query_type = "NEW_TOPIC"

        logger.info(f"Memory Manager updated filters: {new_filters}")
        logger.info(f"Memory Manager query_type: {query_type}")

        return {
            "query_type": query_type,
            "active_filters": new_filters,
            "verified_joins": new_joins,
            "error_log": error_log,
            "negative_constraints": negative_constraints,
            "confirmed_tables": confirmed_tables,
            "history_tables": history_tables
        }
