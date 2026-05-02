import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import asyncpg
import sqlglot
from sqlglot import exp

from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG
from axiom.core.inference import AdaptiveInferenceManager
from axiom.agent.lake_worker import LakeWorker, LakeWorkerResult
from axiom.agent.prompt_registry import registry as _prompt_registry
from axiom.agent.toolkits import CRITIC_TOOLS, investigation_toolkit
from axiom.agent.shared_memory import (
    SchemaContract, SchemaContractNegotiator, shared_memory,
)

logger = logging.getLogger(__name__)


import ipaddress

def _to_json(rows: list, cols: list[str]) -> str:
    def _convert(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Interface, ipaddress.IPv6Interface, ipaddress.IPv4Network, ipaddress.IPv6Network)):
            return str(v)
        return v
    return json.dumps(
        {"columns": cols, "rows": [[_convert(v) for v in row] for row in rows]},
        default=str
    )


def _get_content(response) -> str:
    """Safely extract string content from OpenAI/LiteLLM response."""
    content = response.choices[0].message.content
    if content is None:
        return ""
    if isinstance(content, list):
        # Handle content blocks (e.g. from some Anthropic/Gemini models via LiteLLM)
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        content_str = "".join(parts)
    else:
        content_str = str(content)
    
    # Remove <think> blocks (often present in reasoning models) to avoid breaking JSON parsing
    content_str = re.sub(r"<think>.*?</think>", "", content_str, flags=re.DOTALL)
    return content_str


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

        tenant_id = state.get("tenant_id", "")
        question = state.get("question", "")

        # 1. Determine the source pool: lake_scope > lake_id > all active sources
        lake_scope = state.get("lake_scope") or []
        lake_id = state.get("lake_id")
        cp_conn = await asyncpg.connect(settings.database_url)
        try:
            if lake_scope:
                # User explicitly scoped to specific sources
                sources = await cp_conn.fetch(
                    """SELECT source_id, description, db_type, custom_rules
                       FROM data_sources
                       WHERE tenant_id = $1 AND status = 'active' AND source_id = ANY($2::text[])""",
                    tenant_id, lake_scope
                )
            else:
                # Use lake_id from state or fall back to the first available lake
                target_lake = lake_id
                if not target_lake:
                    target_lake = await cp_conn.fetchval(
                        "SELECT id FROM lakes WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                        tenant_id
                    )

                if target_lake:
                    lake_sources = await cp_conn.fetch(
                        "SELECT source_id FROM lake_sources WHERE lake_id = $1", target_lake
                    )
                    ids = [r["source_id"] for r in lake_sources]
                    sources = await cp_conn.fetch(
                        """SELECT source_id, description, db_type, custom_rules
                           FROM data_sources
                           WHERE tenant_id = $1 AND status = 'active' AND source_id = ANY($2::text[])""",
                        tenant_id, ids
                    )
                else:
                    # No lake configured — search all active sources
                    sources = await cp_conn.fetch(
                        """SELECT source_id, description, db_type, custom_rules
                           FROM data_sources WHERE tenant_id = $1 AND status = 'active'""",
                        tenant_id
                    )
        finally:
            await cp_conn.close()

        # Partition sources by query mode.
        # get_query_mode returns None for app connectors (Gmail, Slack, …) that are stored
        # in data_sources but are not queryable by the database worker pipeline.
        # Adding any new database connector (Snowflake, BigQuery, Redshift, DuckDB, …) only
        # requires registering it with ConnectorFactory — no changes needed here.
        from axiom.connectors.factory import ConnectorFactory
        from axiom.connectors.base import QueryMode

        db_sources = []
        skipped_app = []
        for s in sources:
            mode = await ConnectorFactory.get_query_mode(s["db_type"])
            if mode is None:
                skipped_app.append(s["source_id"])
            else:
                s = dict(s)
                s["_query_mode"] = mode
                db_sources.append(s)

        if skipped_app:
            logger.info(
                "Lake routing: skipped %d app connector(s) not queryable as databases: %s",
                len(skipped_app), skipped_app,
            )

        sources = db_sources

        if not sources:
            if lake_id:
                # Lake explicitly selected but contains no active SQL-queryable sources.
                # Return an error that _route_after_db_selection will catch and short-circuit.
                logger.warning(
                    "Lake %s has no active SQL-queryable sources for tenant %s. "
                    "Skipped app connectors: %s",
                    lake_id, tenant_id, skipped_app,
                )
                return {
                    "source_id": None,
                    "lake_mode": False,
                    "error": (
                        "The selected lake has no active SQL-queryable data sources. "
                        "Add a PostgreSQL, MySQL, or MongoDB connection to the lake and "
                        "make sure it has been ingested successfully."
                    ),
                }
            logger.warning("No active data sources configured for tenant %s.", tenant_id)
            return {
                "source_id": None,
                "lake_mode": False,
                "error": "No active data sources found. Connect and ingest a database first.",
            }

        if len(sources) == 1:
            logger.info("Single source, auto-selecting: %s", sources[0]["source_id"])
            return {
                "source_id": sources[0]["source_id"],
                "db_type": sources[0]["db_type"],
                "custom_rules": sources[0]["custom_rules"] or "",
                "needs_source_clarification": False,
                "routing_candidates": [],
                "lake_mode": False,
            }

        # 2. Lake fan-out: when an explicit lake_id is set, activate multi-source mode.
        #    lake_scope carries (source_id, query_mode) pairs so LakeOrchestratorNode
        #    can dispatch the right worker type per source without any hardcoding.
        if lake_id:
            scope = [
                {"source_id": s["source_id"], "query_mode": s["_query_mode"]}
                for s in sources
            ]
            logger.info(
                "Lake mode activated for lake_id=%s — fanning out to %d sources: %s",
                lake_id,
                len(scope),
                [(e["source_id"], e["query_mode"]) for e in scope],
            )
            return {
                "lake_scope": [e["source_id"] for e in scope],  # keep plain list for state compat
                "lake_scope_meta": scope,                        # rich list for orchestrator dispatch
                "lake_mode": True,
                "needs_source_clarification": False,
                "routing_candidates": [],
            }

        # 3. No explicit lake — LLM routing with confidence scoring
        history_context = state.get("history_context", "")
        
        source_list = "\n".join(
            [f'- {s["source_id"]} ({s["db_type"]}): {s["description"] or "no description"}' for s in sources]
        )
        prompt = f"""You are a database router. A user asked: "{question}"

### CONVERSATION HISTORY (to help understand ambiguous references like "this"):
{history_context}

### AVAILABLE DATABASES:
{source_list}

Score each database's relevance (0.0–1.0) and pick the best match.
Respond ONLY with valid JSON:
{{
  "selected": "<source_id>",
  "confidence": <0.0-1.0>,
  "candidates": [
    {{"source_id": "<id>", "reason": "<one sentence>", "score": <0.0-1.0>}}
  ]
}}
Include ALL databases in candidates, sorted by score descending."""

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            result = json.loads(_get_content(response).strip())
            selected_id = result.get("selected", "")
            confidence = float(result.get("confidence", 1.0))
            candidates = result.get("candidates", [])
        except Exception:
            selected_id = ""
            confidence = 1.0
            candidates = []

        selected_source = next((s for s in sources if s["source_id"] == selected_id), sources[0])
        selected_id = selected_source["source_id"]
        db_type = selected_source["db_type"]
        custom_rules = selected_source["custom_rules"] or ""

        # 4. Low confidence → surface ambiguity to the user (HITL)
        CONFIDENCE_THRESHOLD = 0.65
        if confidence < CONFIDENCE_THRESHOLD and len(sources) > 1:
            logger.info(
                "Routing ambiguous (confidence=%.2f) for tenant %s — surfacing to user",
                confidence, tenant_id,
            )
            return {
                "source_id": selected_id,
                "db_type": db_type,
                "custom_rules": custom_rules,
                "needs_source_clarification": True,
                "routing_candidates": candidates,
                "lake_mode": False,
            }

        logger.info("Routed to %s (%s) confidence=%.2f", selected_id, db_type, confidence)
        return {
            "source_id": selected_id,
            "db_type": db_type,
            "custom_rules": custom_rules,
            "needs_source_clarification": False,
            "routing_candidates": [],
            "lake_mode": False,
        }



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

        # 2. Get RAG Candidates (Dual-Vector Search)
        # Search summaries for semantic/intent match
        summaries = await self._rag.search_table_summaries(tenant_id, source_id, search_query, n_results=15)
        # Search DDLs for column/structure match
        ddl_tables = await self._rag.search_tables(tenant_id, source_id, search_query, n_results=10)
        
        # 3. Perform a 'Value Sniff' for entity matches (The 'embeddings we need' alternative)
        sniffed_tables = []
        sniff_keywords = [k for k in search_query.split() if len(k) > 4 and k.lower() not in ["show", "tell", "list", "query", "what", "where", "when"]]
        if sniff_keywords:
            try:
                cp_conn = await asyncpg.connect(settings.database_url, timeout=5)
                try:
                    row = await cp_conn.fetchrow("SELECT db_url FROM data_sources WHERE source_id = $1", source_id)
                    if row:
                        target_conn = await asyncpg.connect(row["db_url"], timeout=5)
                        try:
                            all_cols = await DynamicSchemaMapper.get_searchable_columns(target_conn)
                            for term in sniff_keywords[:2]:
                                hits = await DynamicSchemaMapper.sniff_value(target_conn, term, all_cols)
                                sniffed_tables.extend([h.table for h in hits])
                        finally:
                            await target_conn.close()
                finally:
                    await cp_conn.close()
            except Exception:
                pass

        # 4. Prepare Candidate Text for LLM
        summary_text = "\n".join([f"- {s['table']}: {s['summary']}" for s in summaries])
        
        for t in ddl_tables:
            if t not in [s['table'] for s in summaries]:
                summary_text += f"\n- {t}: Potential match based on column structure."
        
        for t in grepped_tables:
            if t not in [s['table'] for s in summaries] and t not in ddl_tables:
                summary_text += f"\n- {t}: Potential match found via column name grep."
                
        for t in sniffed_tables:
            if t not in [s['table'] for s in summaries] and t not in ddl_tables and t not in grepped_tables:
                summary_text += f"\n- {t}: Found actual data matching '{sniff_keywords[0]}' in this table."

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

        if not summaries and not ddl_tables and not grepped_tables and not sniffed_tables and not history_tables and not confirmed_tables:
            return {"selected_tables": []}
            
        summary_text += history_summaries_text
        
        from axiom.agent.prompt_registry import registry
        system_msg = registry.render_system("table_strategy")
        context_msg = registry.render_context(
            "table_strategy",
            summary_text=summary_text,
            question=search_query
        )

        # Get Wide Routing Parameters
        params = AdaptiveInferenceManager.get_parameters("routing", 0)

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": context_msg}
            ],
            **params
        )
        
        try:
            content = _get_content(response).strip()
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

        history = state.get("history_context", "")
        if history and "No prior" not in history:
            try:
                last_q = history.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {search_query}"
            except Exception:
                pass

        from axiom.security.trust.ans import AgentNamingService
        session_did = AgentNamingService.generate_session_did(tenant_id, state.get("session_id", "default"))
        agent_did = AgentNamingService.generate_agent_did("knowledge_retrieval", session_did)
        logger.info(f"Agent {agent_did} initiated schema retrieval (direct RAG)")

        if selected_tables:
            schema_context = await self._rag.retrieve_exact(tenant_id, source_id, selected_tables)
        else:
            schema_context = await self._rag.retrieve(tenant_id, source_id, search_query, n_results=5)

        few_shot_examples = await self._rag.retrieve_examples(tenant_id, source_id, search_query, n_results=2)

        return {
            "schema_context": schema_context,
            "few_shot_examples": few_shot_examples
        }

class SQLPlannerNode:
    """
    Strategic SQL Architect (CAMEL-inspired Task Specifier).
    Defines the logical blueprint, granularity, and join path before any SQL is written.
    """

    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        question = state.get("question", "")
        history = state.get("history_context", "No prior history.")
        schema = state.get("schema_context", "")

        from axiom.agent.prompt_registry import registry
        system_msg = registry.render_system("sql_planner")
        context_msg = registry.render_context(
            "sql_planner",
            question=question,
            history_context=history,
            schema_context=schema
        )

        try:
            resp = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_msg}
                ],
                temperature=0.0,
            )
            blueprint = _get_content(resp).strip()
            logger.info("SQL Blueprint generated for thread %s", state.get("thread_id"))
            return {"logical_blueprint": blueprint}
        except Exception as exc:
            logger.warning("SQLPlannerNode failed: %s", exc)
            return {"logical_blueprint": None}


class SQLGenerationNode:
    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def _build_prompt(self, state: SQLAgentState) -> tuple[str, str]:
        """Return (system_message, context_message) assembled from the prompt registry."""
        schema_context = state.get("schema_context", "")
        question = state.get("question", "")
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

        # Pre-process conditional slots before handing to registry
        if confirmed_tables:
            confirmed_tables_block = (
                json.dumps(confirmed_tables) + "\n"
                "CRITICAL: If a table is listed here, the user explicitly chose it. "
                "You MUST prioritize it as the primary table for your query. However, "
                "you are ALLOWED and ENCOURAGED to use ANY OTHER tables from the SCHEMA "
                "CONTEXT to fully answer the question."
            )
        else:
            confirmed_tables_block = "None. You have full autonomy to select ANY tables from the SCHEMA CONTEXT."

        if negative_constraints:
            negative_constraints_block = (
                json.dumps(negative_constraints, indent=2) + "\n"
                "CRITICAL: If a table or join path is listed in Negative Constraints, "
                "it was flagged as WRONG by the user. You are FORBIDDEN from using it. "
                "If you cannot answer the question without these tables, use the <error> "
                "tags to explain why, rather than repeating a failed path."
            )
        else:
            negative_constraints_block = "None"

        extra_footer = ""
        if critic_feedback:
            extra_footer = (
                f"\n\n### CRITIC FEEDBACK (PREVIOUS ATTEMPT FAILED):\n{critic_feedback}\n\n"
                "Update your query strictly following this technical feedback."
            )
        elif error:
            extra_footer = (
                f"\n\n### PREVIOUS ATTEMPT FAILED:\n{error}\n\n"
                "Review the SCHEMA CONTEXT carefully.\n"
                '- If the error is "relation ... does not exist", you likely forgot the schema prefix '
                '(e.g. use "public"."tableName" instead of "tableName").\n'
                "- If the error suggests a column or table name that exists but with different "
                'capitalization, you MUST use double quotes around that name (e.g., "membershipFees").\n'
                '- If the error is "function ... does not exist" and mentions argument types, you MUST '
                "explicitly cast the column to numeric or the appropriate type "
                "(e.g., `SUM(column::numeric)`, `AVG(CAST(column AS numeric))`)."
            )

        system_msg = _prompt_registry.render_system("sql_generator")
        context_msg = _prompt_registry.render_context(
            "sql_generator",
            dialect_name=dialect_name.upper(),
            dialect_rules=dialect_rules,
            schema_context=schema_context,
            confirmed_tables_block=confirmed_tables_block,
            negative_constraints_block=negative_constraints_block,
            history_context=history_context or "No prior history.",
            custom_rules=custom_rules or "None",
            logical_blueprint=logical_blueprint or "No blueprint provided. Determine logic directly.",
            few_shot_examples=few_shot_examples or "No past examples available.",
            query_type=query_type,
            question=question,
        ) + extra_footer

        return system_msg, context_msg

    async def __call__(self, state: SQLAgentState) -> dict:
        attempts = state.get("attempts", 0)
        error = state.get("error")

        # Fail fast: if there is no schema context and no previous error driving a retry,
        # the database was unreachable or never ingested. Don't burn LLM calls — the LLM
        # will correctly say "no schema" every single time.
        schema_context = state.get("schema_context", "")
        if not schema_context and not error:
            source_id = state.get("source_id") or "unknown"
            return {
                "sql_query": "",
                "error": (
                    f"No schema available for source '{source_id}'. "
                    "Ensure the data source is connected and has been ingested (synced) successfully."
                ),
                "attempts": settings.max_correction_attempts,  # skip retry loop
            }

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
        adaptive_override = AdaptiveInferenceManager.get_system_override("generation")

        base_system_msg, context_msg = await self._build_prompt(state)
        system_content = base_system_msg
        if adaptive_override:
            system_content = f"{base_system_msg}\n\n{adaptive_override}"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": context_msg},
        ]

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=messages,
            **params
        )
        content = _get_content(response).strip()
        
        # Log thought process for debugging
        thought = ""
        thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()
            logger.info("Agent Thought: %s", thought)

        # Extract error from <error> tags if present (e.g. semantic impossibility)
        error_match = re.search(r"<error>(.*?)</error>", content, re.DOTALL)
        if error_match:
            error_msg = error_match.group(1).strip()
            updated_log = list(state.get("error_log") or []) + [error_msg]
            return {
                "sql_query": "",
                "error": error_msg,
                "agent_thought": thought,
                "attempts": settings.max_correction_attempts,
                "error_log": updated_log,
            }

        # Extract SQL from <sql> tags
        sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
        if sql_match:
            sql = sql_match.group(1).strip()
        else:
            # Fallback if tags are missing — strip thought block and markdown fences
            sql = re.sub(r"<thought>.*?</thought>", "", content, flags=re.DOTALL)
            sql = sql.replace("```sql", "").replace("```", "").strip()
            
        # PURE VISUALIZATION REFINEMENT FALLBACK:
        # If the LLM was lazy and didn't output SQL (or output conversational text)
        # because it's just a chart change, automatically borrow the last working SQL from the history.
        if (not sql or not sql.upper().startswith("SELECT")) and state.get("query_type") == "REFINEMENT":
            history_context = state.get("history_context", "")
            sql_matches = re.findall(r"SQL:\s*(.*?)\nResult:", history_context, re.DOTALL)
            if sql_matches:
                sql = sql_matches[-1].strip()
                logger.info("SQLGenerationNode output invalid/empty SQL during REFINEMENT. Auto-reusing last SQL from history.")
            
        return {"sql_query": sql, "error": None, "agent_thought": thought, "attempts": state.get("attempts", 0) + 1}


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

            system_msg = _prompt_registry.render_system("sql_critic_zero_results")
            prompt = _prompt_registry.render_context(
                "sql_critic_zero_results",
                question=question,
                schema_context=schema_context,
                custom_rules=custom_rules or "None",
                sql_query=sql_query,
                table_catalog=table_catalog,
                data_probe=data_probe or "No samples available.",
            )
        else:
            system_msg = _prompt_registry.render_system("sql_critic_syntax")
            prompt = _prompt_registry.render_context(
                "sql_critic_syntax",
                question=question,
                schema_context=schema_context,
                custom_rules=custom_rules or "None",
                sql_query=sql_query,
                error=error,
            )

        async def _dispatch_tool(name: str, args: dict) -> str:
            connector, _ = await self._get_connector(state)
            if connector is None:
                return "Tool error: source not found."
            return await investigation_toolkit.dispatch(name, args, connector)

        # ── LLM loop with tool use ────────────────────────────────────────────
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]
        params = AdaptiveInferenceManager.get_parameters("critic", state.get("attempts", 0), error)
        max_tool_calls = 8 if is_zero_results else 3
        tool_calls_made = 0

        while True:
            try:
                response = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=messages,
                    tools=CRITIC_TOOLS,
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
                content = _get_content(response).strip()

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

        sql = (state.get("sql_query") or "").strip()
        if not sql:
             return {"sql_result": None, "error": "No SQL query generated."}

        db_type = state.get("db_type", "postgresql")

        # MongoDB pipelines are JSON — skip the SQL AST security check entirely.
        # The connector itself is read-only (aggregate only, no write operations).
        if db_type != "mongodb":
            safe, sec_error = await self._is_read_only(sql, db_type)
            if not safe:
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
                
                # Zero Trust: log agent identity for audit trail
                from axiom.security.trust.ans import AgentNamingService
                session_did = AgentNamingService.generate_session_did(state.get("tenant_id", "default"), state.get("session_id", "default"))
                agent_did = AgentNamingService.generate_agent_did("sql_execution", session_did)
                logger.info(f"Agent {agent_did} executing SQL directly on {db_type}://{source_id}")
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
            error_msg = str(exc)
            updated_log = list(state.get("error_log") or []) + [error_msg]
            result_update = {"sql_result": None, "error": error_msg, "error_log": updated_log}

        if self._thread_mgr and state.get("sql_query") and not result_update.get("error"):
            await self._thread_mgr.save_turn(
                state["thread_id"],
                state.get("tenant_id", "default"),
                state["question"],
                state.get("sql_query", ""),
                result_update.get("sql_result", ""),
                active_filters=state.get("active_filters", []),
                verified_joins=state.get("verified_joins", []),
                error_log=state.get("error_log", []),
                llm_model=state.get("llm_model"),
                source_id=state.get("source_id"),
            )
        
        return result_update

class DataValidatorNode:
    """
    Data Quality Evaluator.
    Checks SQL results to ensure they contain valid, non-null data for the key metrics.
    If the data is essentially empty/null, it returns an error so the pipeline can correct itself or abort gracefully.
    """

    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        raw_result = state.get("sql_result")
        
        # If no result, or already has an error, skip validation
        if not raw_result or state.get("error") or raw_result == "CONCLUDED":
            return {}

        try:
            result_json = json.loads(raw_result)
            columns: list = result_json.get("columns", [])
            rows: list = result_json.get("rows", [])
            
            # Basic sanity check
            if not rows:
                return {"error": "ZERO_RESULTS: The query executed successfully but returned 0 rows.", "sql_result": None}

            from axiom.agent.prompt_registry import registry
            system_msg = registry.render_system("data_validator")
            context_msg = registry.render_context(
                "data_validator",
                question=state.get("question", ""),
                sql_query=state.get("sql_query", ""),
                columns=columns,
                sample_rows=rows[:5]
            )

            resp = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_msg}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(_get_content(resp).strip())
            is_valid = data.get("is_valid", True)
            feedback = data.get("feedback", "")
            
            if not is_valid:
                logger.warning("Data Validation Failed: %s", feedback)
                # Nullify the result and pass the feedback as an error to trigger correction
                return {
                    "sql_result": None,
                    "error": f"DATA_QUALITY_ERROR: {feedback}"
                }
                
            return {} # Valid data, proceed

        except Exception as exc:
            logger.warning("DataValidatorNode failed (passing through): %s", exc)
            return {}

class VisualizationNode:
    """
    WOW-Factor Plotly Visualization generator.
    Transforms SQL results into a stunning, interactive Python dashboard.
    """

    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        raw_result = state.get("sql_result") or state.get("last_sql_result")
        mcp_results = state.get("mcp_tool_results", [])

        if (not raw_result or raw_result == "CONCLUDED") and not mcp_results:
            return {"python_code": None}

        from axiom.agent.prompt_registry import registry
        
        try:
            question = state["question"]
            error = state.get("python_error")
            
            boilerplate = """
# ── Axiom Plotly Template Setup ──
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.io as pio

pio.templates["axiom_dark"] = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E6E1D8"),
        colorway=["#638A70", "#C26D5C", "#A5A58D", "#6B705C", "#B7B7A4"],
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.1)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.1)"),
        hoverlabel=dict(bgcolor="#1E1E1C", bordercolor="rgba(255,255,255,0.1)")
    )
)
pio.templates.default = "axiom_dark"
"""
            
            # ── SQL result path (Structured) ──────────────────────────────────
            if raw_result and raw_result != "CONCLUDED":
                result_json = json.loads(raw_result)
                columns: list = result_json.get("columns", [])
                rows: list = result_json.get("rows", [])

                if not columns or not rows:
                    return {"python_code": None}

                system_msg = registry.render_system("visualization_expert")
                context_msg = registry.render_context(
                    "visualization_expert",
                    question=question,
                    columns=columns,
                    sample_rows=rows[:2]
                )
                
                if error:
                    context_msg += f"\n\nPREVIOUS ATTEMPT FAILED:\n{error}\nFix the code."

                resp = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": context_msg}
                    ],
                    temperature=0.0
                )
                code = re.sub(r"```python\n?|```", "", _get_content(resp).strip())
                return {"python_code": boilerplate + "\n\n" + code}

            # ── App connector path (Unstructured) ─────────────────────────────
            if mcp_results:
                sample = ""
                for t in mcp_results:
                    if t.get("result"):
                        sample = str(t["result"])[:1000]
                        break
                
                system_msg = registry.render_system("visualization_expert")
                context_msg = f"Convert this unstructured data into a stunning Plotly chart.\nQuestion: {question}\nData: {sample}"
                
                if error:
                    context_msg += f"\n\nPREVIOUS ATTEMPT FAILED:\n{error}\nFix the code."

                resp = await self._client.chat.completions.create(
                    model=state.get("llm_model") or settings.llm_model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": context_msg}
                    ],
                    temperature=0.0
                )
                code = re.sub(r"```python\n?|```", "", _get_content(resp).strip())
                return {"python_code": boilerplate + "\n\n" + code}

        except Exception as exc:
            logger.warning("VisualizationNode failed: %s", exc)
            return {"python_code": None}

        return {"python_code": None}


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
        mcp_results = state.get("mcp_tool_results", [])
        
        if state.get("error") or ((not raw_result or raw_result == "CONCLUDED") and not mcp_results):
            return {"artifact": None}

        from axiom.notebooks.builder import build_analysis_notebook

        artifact_id = str(uuid.uuid4())
        tenant_id = state.get("tenant_id", "default_tenant")
        thread_id = state.get("thread_id", artifact_id)

        try:
            result_for_notebook = None
            if raw_result and raw_result != "CONCLUDED":
                result_json = json.loads(raw_result)
                columns = result_json.get("columns", [])
                rows = result_json.get("rows", [])
                if not isinstance(columns, list) or not isinstance(rows, list):
                    raise ValueError("SQL result must contain list-valued columns and rows")
                result_for_notebook = result_json
            elif mcp_results:
                # App Connector path - pass the raw results list 
                results_list = []
                for t in mcp_results:
                    if t.get("result"):
                        try:
                            # Try to parse as JSON if it's a string, to avoid double-encoding
                            if isinstance(t["result"], str):
                                parsed = json.loads(t["result"])
                                # If it's a list of dicts, extend
                                if isinstance(parsed, list):
                                    results_list.extend(parsed)
                                else:
                                    results_list.append(parsed)
                            else:
                                results_list.append(t["result"])
                        except Exception:
                            # Fallback: Just wrap the raw text in a dict
                            results_list.append({"raw_text": str(t["result"])})
                result_for_notebook = results_list
                if not result_for_notebook:
                    return {"artifact": None}

            # Don't embed a clarification/probing message as the notebook insight —
            # it's internal system state, not meaningful analysis context.
            _insight = state.get("response_text")
            _clarification_prefixes = (
                "I need a bit more context",
                "I found multiple tables",
                "I found multiple databases",
            )
            if _insight and any(_insight.startswith(p) for p in _clarification_prefixes):
                _insight = None

            notebook, cells_summary = build_analysis_notebook(
                question=state["question"],
                sql=state.get("sql_query") or "",
                result=result_for_notebook,
                insight=_insight,
                python_code=state.get("python_code"),
            )

            execution = await self._executor.execute(
                tenant_id=tenant_id,
                thread_id=thread_id,
                artifact_id=artifact_id,
                notebook=notebook,
            )

            execution_error = execution.get("execution_error")
            notebook_attempts = state.get("notebook_attempts", 0) + 1

            # If execution failed and we haven't exhausted retries, feed the error
            # back to VisualizationNode for self-correction.
            if execution_error and notebook_attempts <= 3:
                logger.warning(
                    "Notebook execution failed (attempt %d/3): %s",
                    notebook_attempts, execution_error[:200],
                )
                return {
                    "python_error": execution_error,
                    "notebook_attempts": notebook_attempts,
                    "artifact": None,
                }

            # Success or exhausted retries — save whatever we have.
            artifact = self._store.save(
                artifact_id=artifact_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
                notebook=execution.get("notebook") or notebook,
                status=execution.get("status", "failed"),
                outputs=execution.get("outputs", []),
                cells_summary=cells_summary,
                execution_error=execution_error,
                logs=execution.get("logs"),
            )
            return {"artifact": artifact, "python_error": None, "notebook_attempts": 0}
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
                python_code=state.get("python_code"),
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
            return {"artifact": artifact, "python_error": None, "notebook_attempts": 0}


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
        if state.get("clarification_questions"):
            return {
                "response_text": "I need a bit more context to answer precisely. Please select from the options below.",
            }

        if state.get("probing_options"):
            return {
                "response_text": "I found multiple tables that could answer your question. Which one did you mean?",
            }

        if state.get("needs_source_clarification"):
            return {
                "response_text": "I found multiple databases that could answer your question. Which one did you mean?",
            }

        if state.get("error"):
            error = state.get("error", "")
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
                    return {"response_text": _get_content(response).strip()}
                except Exception:
                    msg = f"No results found for your query."
                    if clean_signal:
                        msg += f" {clean_signal}."
                    return {"response_text": msg}
            return {"response_text": f"I encountered an error: {error}"}

        # RCA path: ActionPlanNode already populated response_text and cleared sql_result
        if state.get("response_text") and not state.get("sql_result") and not state.get("mcp_tool_results"):
            artifact = state.get("artifact")
            return {
                "response_text": state.get("response_text", ""),
                "layout": "notebook" if artifact else "default",
                "action_bar": [],
            }

        sql_result = state.get("sql_result")
        mcp_results = state.get("mcp_tool_results", [])

        if not sql_result and not mcp_results:
            return {"response_text": "I couldn't find any data to answer your question."}

        question = state["question"]
        artifact = state.get("artifact")
        
        # 1. Handle SQL Path
        if sql_result:
            from axiom.core.cleansing import MLGradeInterceptor
            interceptor = MLGradeInterceptor()
            
            # Apply deterministic ML-grade data cleaning
            cleaned_response = interceptor.process(sql_result, anomaly_method="iqr")
            cleaned_data = cleaned_response.data
            metadata = cleaned_response.metadata
            
            if not cleaned_data:
                return {"response_text": "The query returned no results."}

            columns = list(cleaned_data[0].keys()) if cleaned_data else []
            sample = cleaned_data[:5]
            
            data_context = f"""### DATA RESULTS (Columns: {columns}):
{sample}

### METADATA:
- Original Row Count: {metadata.row_count_original}
- Cleaned Row Count: {metadata.row_count_cleaned}
- Summary Stats: {json.dumps(metadata.summary_stats, indent=2)}"""
            metadata_summary = f"Rows: {metadata.row_count_cleaned}, Stats: {json.dumps(metadata.summary_stats)}"
        
        # 2. Handle App Path
        else:
            # For apps, we use the response_text from the AppExecutionNode as the primary insight,
            # but we can synthesize a better one if needed from the tool results.
            app_insight = state.get("response_text", "")
            data_context = f"""### APP TOOL RESULTS:
{json.dumps(mcp_results, indent=2)[:5000]}

### PRELIMINARY INSIGHT:
{app_insight}"""
            metadata_summary = "N/A (App Data)"

        from axiom.agent.prompt_registry import registry
        system_msg = registry.render_system("business_analyst")
        context_msg = registry.render_context(
            "business_analyst",
            question=question,
            data_results=data_context,
            metadata_summary=metadata_summary if sql_result else "Unstructured app data"
        )

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_msg}
                ],
                temperature=0.1,
            )
            content = _get_content(response).strip()
            
            insight_match = re.search(r"<insight>(.*?)</insight>", content, re.DOTALL)
            kpi_match = re.search(r"<kpis>(.*?)</kpis>", content, re.DOTALL)
            
            insight = insight_match.group(1).strip() if insight_match else content.strip()
            kpis = []
            if kpi_match:
                try:
                    kpis = json.loads(kpi_match.group(1).strip())
                except Exception:
                    pass

            layout = "default"
            if state.get("visual_manifest") or artifact:
                layout = "dashboard"

            res = {
                "response_text": insight,
                "insight_kpis": kpis,
                "layout": layout,
                "action_bar": cleaned_response.action_bar if sql_result else [],
            }
            if sql_result:
                res["sql_result"] = cleaned_response.frontend_json
            return res
        except Exception as exc:
            logger.warning("Failed to synthesize response: %s", exc)
            return {"response_text": state.get("response_text", "I have processed your request.")}


class LakeOrchestratorNode:
    """
    Fan-out node for Data Lake queries.

    Spawns one LakeWorker per source in lake_scope, runs them concurrently
    under a configurable semaphore, and collects results.  Worker failures are
    isolated — a single bad source never aborts the entire fan-out.
    """

    def __init__(self, rag: SchemaRAG) -> None:
        self._rag = rag
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> dict:
        from axiom.connectors.base import QueryMode

        # Prefer the rich meta list; fall back to plain scope with unknown mode
        scope_meta: list[dict] = state.get("lake_scope_meta") or []
        lake_scope: list[str] = state.get("lake_scope") or []
        if not scope_meta and lake_scope:
            scope_meta = [{"source_id": sid, "query_mode": QueryMode.SQL} for sid in lake_scope]

        if not scope_meta:
            logger.warning("LakeOrchestratorNode called with empty scope — skipping fan-out")
            return {"lake_worker_results": []}

        semaphore = asyncio.Semaphore(settings.lake_max_concurrent_workers)
        workers = []
        for entry in scope_meta:
            sid = entry["source_id"]
            mode = entry.get("query_mode", QueryMode.SQL)
            if mode in (QueryMode.SQL, QueryMode.PIPELINE):
                # LakeWorker handles both SQL databases and pipeline stores (MongoDB).
                # Dialect-specific LLM instructions handle the query language differences.
                workers.append((sid, LakeWorker(sid, self._rag, self._client)))
            else:
                # APP connectors in a lake are not yet dispatchable via LakeWorker.
                # AppLakeWorker (tool-use loop) is planned; log clearly for now.
                logger.warning(
                    "Lake fan-out: source '%s' has query_mode=%s — app connectors in lakes "
                    "require AppLakeWorker (not yet implemented). Skipping.",
                    sid, mode,
                )

        # Load existing schema contracts for this thread from shared memory.
        # On first query the list is empty; on follow-up queries it holds
        # column-mapping hints negotiated by the Curator on the previous turn.
        thread_id = state.get("thread_id", "")
        existing_contracts = await shared_memory.read_contracts(thread_id)
        # Also pull any contracts already serialised into graph state
        state_contracts = [
            SchemaContract.from_dict(d)
            for d in (state.get("schema_contracts") or [])
        ]
        all_contracts = shared_memory._merge_contracts(existing_contracts, state_contracts)

        tasks = [
            w.run(
                question=state["question"],
                tenant_id=state["tenant_id"],
                llm_model=state.get("llm_model"),
                semaphore=semaphore,
                history_context=state.get("history_context", ""),
                query_type=state.get("query_type", "NEW_TOPIC"),
                schema_contract_hint="\n".join(
                    hint
                    for c in shared_memory.contracts_for_source(all_contracts, sid)
                    if (hint := c.prompt_hint(sid))
                ),
            )
            for sid, w in workers
        ]
        source_ids = [sid for sid, _ in workers]

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        worker_results: list[dict] = []
        for sid, result in zip(source_ids, raw_results):
            if isinstance(result, Exception):
                logger.error("LakeWorker %s raised: %s", sid, result)
                worker_results.append(
                    LakeWorkerResult.failure(sid, "unknown", str(result)).to_dict()
                )
            else:
                worker_results.append(result.to_dict())
                logger.info(
                    "Worker %-40s  rows=%3d  error=%-30s  %.0fms",
                    result.source_id, result.row_count,
                    result.error or "none", result.duration_ms,
                )

        return {"lake_worker_results": worker_results}


class LakeCuratorNode:
    """
    Synthesises results from all lake workers into a single unified response.

    Merges row-level data where schemas are compatible (adds a _source column),
    and uses an LLM to produce a cross-source narrative answer.
    """

    def __init__(self) -> None:
        import openai
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    # ------------------------------------------------------------------ #
    #  Result merging                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _merge_results(worker_results: list[dict]) -> Optional[str]:
        """
        Merge successful per-source results into a single result JSON.

        If all sources share the same column set, rows are interleaved with a
        leading _source column.  When schemas differ, we still emit a _source
        column but pad missing columns with None so the frontend table renders
        consistently.
        """
        successful = [r for r in worker_results if r.get("sql_result")]
        if not successful:
            return None

        # Collect per-source parsed data
        parsed: list[tuple[str, list, list]] = []  # (source_id, columns, rows)
        for r in successful:
            try:
                data = json.loads(r["sql_result"])
                parsed.append((r["source_id"], data.get("columns", []), data.get("rows", [])))
            except Exception:
                continue

        if not parsed:
            return None

        # Build superset of columns across all sources
        seen: dict[str, None] = {}
        for _, cols, _ in parsed:
            for c in cols:
                seen.setdefault(c, None)
        all_columns = ["_source"] + list(seen)

        merged_rows: list[list] = []
        for source_id, cols, rows in parsed:
            col_index = {c: i for i, c in enumerate(cols)}
            for row in rows:
                if isinstance(row, list):
                    row_dict = {cols[i]: v for i, v in enumerate(row) if i < len(cols)}
                elif isinstance(row, dict):
                    row_dict = row
                else:
                    continue
                merged_rows.append(
                    [source_id] + [row_dict.get(c) for c in all_columns[1:]]
                )

        return json.dumps(
            {
                "columns": all_columns,
                "rows": merged_rows[:100],
                "total_count": len(merged_rows),
                "is_lake_result": True,
                "source_count": len(parsed),
            },
            default=str,
        )

    # ------------------------------------------------------------------ #
    #  LLM narrative synthesis                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_source_summary(worker_results: list[dict]) -> str:
        parts: list[str] = []
        for r in worker_results:
            if r.get("sql_result"):
                try:
                    data = json.loads(r["sql_result"])
                    sample = data["rows"][:3]
                    parts.append(
                        f"**{r['source_id']}** ({r['db_type']})\n"
                        f"  SQL: {r.get('sql_query', 'N/A')}\n"
                        f"  Rows returned: {r['row_count']}\n"
                        f"  Columns: {data.get('columns', [])}\n"
                        f"  Sample: {json.dumps(sample, default=str)}"
                    )
                except Exception:
                    parts.append(f"**{r['source_id']}**: result parse error")
            else:
                parts.append(
                    f"**{r['source_id']}** ({r['db_type']}): "
                    f"no results — {r.get('error', 'unknown')}"
                )
        return "\n\n".join(parts)

    async def __call__(self, state: SQLAgentState) -> dict:
        worker_results: list[dict] = state.get("lake_worker_results") or []
        question = state.get("question", "")

        successful = [r for r in worker_results if r.get("sql_result")]
        failed = [r for r in worker_results if r.get("error")]

        if not successful:
            per_source = "; ".join(
                f"{r['source_id']} ({r['db_type']}): {r.get('error', 'no data')}"
                for r in failed
            )
            return {
                "response_text": (
                    f"None of the {len(failed)} data source(s) in this lake returned results "
                    f"for your question. Per-source details: {per_source}"
                ),
                "sql_result": None,
                "error": None,   # curator handled it — don't let synthesizer overwrite with generic message
            }

        source_summary = self._build_source_summary(worker_results)
        skipped_note = (
            "\n\n"
            + "\n".join(
                f"- {r['source_id']} ({r['db_type']}): no results — {r.get('error', 'unknown')}"
                for r in failed
            )
        ) if failed else ""

        prompt = (
            "You are a Senior Data Analyst synthesising query results from multiple "
            "databases in a Data Lake.\n\n"
            f"### USER QUESTION:\n{question}\n\n"
            f"### RESULTS FROM EACH DATA SOURCE:\n{source_summary}{skipped_note}\n\n"
            "### INSTRUCTIONS:\n"
            "1. Write a concise, professional 2–4 sentence response combining all findings.\n"
            "2. If sources returned complementary data, unify the insight.\n"
            "3. If sources returned the same entity type (e.g. users), compare or aggregate.\n"
            "4. If some sources had no results, note it briefly but focus on what was found.\n"
            "5. No SQL, table names, internal IDs, or technical column names in the output.\n"
            "6. No raw rows or JSON — summarise key numbers and trends only.\n"
            "7. Attribute data to a source only when it adds meaningful context.\n\n"
            "Response:"
        )

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            content = response.choices[0].message.content or ""
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
            response_text = content.strip()
        except Exception as exc:
            logger.warning("LakeCuratorNode synthesis failed: %s", exc)
            response_text = (
                f"Found data from {len(successful)} source(s) across your Data Lake."
            )

        # ── Schema contract negotiation ───────────────────────────────────
        # Compare every pair of successful worker results. Any pair whose
        # column sets have meaningful overlap produces a SchemaContract that
        # is persisted to Redis and written into graph state so the next
        # Orchestrator invocation can inject join-key hints into workers.
        negotiator = SchemaContractNegotiator()
        new_contracts: list[SchemaContract] = []
        for i, ra in enumerate(successful):
            for rb in successful[i + 1:]:
                contract = negotiator.negotiate(ra, rb)
                if contract:
                    logger.info(
                        "SchemaContract negotiated: %s ↔ %s  confidence=%.2f  mapping=%s",
                        contract.source_a, contract.source_b,
                        contract.confidence, contract.join_key_mapping,
                    )
                    new_contracts.append(contract)

        thread_id = state.get("thread_id", "")
        if new_contracts:
            await shared_memory.write_contracts(thread_id, new_contracts)

        # Include contracts in state so downstream nodes and future graph
        # resumptions can access them without a Redis round-trip.
        all_state_contracts = list(state.get("schema_contracts") or [])
        for c in new_contracts:
            all_state_contracts.append(c.to_dict())

        merged_sql_result = self._merge_results(worker_results)

        # Promote the best worker's SQL to sql_query so it gets saved to thread history.
        # This lets REFINEMENT follow-up queries (e.g. "give me a pie chart") find the
        # previous SQL via history_context and reuse it without re-querying.
        best_worker = max(successful, key=lambda r: r.get("row_count", 0))
        best_sql = best_worker.get("sql_query") or ""

        return {
            "response_text": response_text,
            "sql_result": merged_sql_result,
            "sql_query": best_sql,
            "schema_contracts": all_state_contracts,
            "layout": "default",
            "action_bar": [],
        }


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
            search_terms = [t.strip() for t in _get_content(res).split(",")]

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
