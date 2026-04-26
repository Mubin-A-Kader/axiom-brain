"""
LakeWorker: runs the schema-retrieval → SQL-generation → SQL-execution
mini-pipeline for a single data source inside a fan-out lake query.

Each LakeWorker is fully stateless and safe to run concurrently under an
asyncio.Semaphore. The orchestrator collects results; the curator synthesises them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

import asyncpg

from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


@dataclass
class LakeWorkerResult:
    source_id: str
    db_type: str
    sql_query: Optional[str]
    # JSON: {"columns": [...], "rows": [...], "total_count": N}
    sql_result: Optional[str]
    row_count: int
    error: Optional[str]
    duration_ms: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def failure(
        cls,
        source_id: str,
        db_type: str,
        error: str,
        duration_ms: float = 0.0,
    ) -> "LakeWorkerResult":
        return cls(
            source_id=source_id,
            db_type=db_type,
            sql_query=None,
            sql_result=None,
            row_count=0,
            error=error,
            duration_ms=duration_ms,
        )


class LakeWorker:
    """Executes the SQL mini-pipeline for one source_id."""

    def __init__(self, source_id: str, rag: SchemaRAG, llm_client) -> None:
        self._source_id = source_id
        self._rag = rag
        self._client = llm_client

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        *,
        question: str,
        tenant_id: str,
        llm_model: Optional[str],
        semaphore: asyncio.Semaphore,
        history_context: str = "",
        query_type: str = "NEW_TOPIC",
    ) -> LakeWorkerResult:
        async with semaphore:
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._pipeline(
                        question=question,
                        tenant_id=tenant_id,
                        llm_model=llm_model or settings.llm_model,
                        history_context=history_context,
                        query_type=query_type,
                    ),
                    timeout=settings.lake_worker_timeout_secs,
                )
                result.duration_ms = (time.monotonic() - t0) * 1000
                return result
            except asyncio.TimeoutError:
                elapsed = (time.monotonic() - t0) * 1000
                logger.warning("LakeWorker %s timed out after %.0fms", self._source_id, elapsed)
                return LakeWorkerResult.failure(
                    self._source_id, "unknown",
                    f"Worker timed out after {settings.lake_worker_timeout_secs}s",
                    elapsed,
                )
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                logger.exception("LakeWorker %s raised unexpected error", self._source_id)
                return LakeWorkerResult.failure(self._source_id, "unknown", str(exc), elapsed)

    # ------------------------------------------------------------------ #
    #  Internal pipeline                                                   #
    # ------------------------------------------------------------------ #

    async def _pipeline(
        self,
        *,
        question: str,
        tenant_id: str,
        llm_model: str,
        history_context: str,
        query_type: str = "NEW_TOPIC",
    ) -> LakeWorkerResult:
        # 1. Load source metadata from control plane
        cp_conn = await asyncpg.connect(settings.database_url)
        try:
            row = await cp_conn.fetchrow(
                """SELECT db_url, db_type, custom_rules, mcp_config
                   FROM data_sources
                   WHERE source_id = $1 AND status = 'active'""",
                self._source_id,
            )
        finally:
            await cp_conn.close()

        if not row:
            return LakeWorkerResult.failure(
                self._source_id, "unknown", "Source not found or not active"
            )

        db_type: str = row["db_type"]
        db_url: str = row["db_url"]
        custom_rules: str = row["custom_rules"] or ""
        mcp_config: dict = json.loads(row["mcp_config"]) if row["mcp_config"] else {}

        # 2. RAG table selection (widen query with last-turn context)
        search_query = question
        if history_context and "No prior" not in history_context:
            try:
                last_q = history_context.split("Q: ")[-1].split("\n")[0]
                search_query = f"{last_q} {question}"
            except Exception:
                pass

        summaries = await self._rag.search_table_summaries(
            tenant_id, self._source_id, search_query, n_results=10
        )
        if not summaries:
            return LakeWorkerResult.failure(
                self._source_id, db_type, "No matching tables in schema"
            )

        selected_tables = [s["table"] for s in summaries[:6]]

        # 3. Schema retrieval
        schema_context = await self._rag.retrieve_exact(
            tenant_id, self._source_id, selected_tables
        )
        few_shot = await self._rag.retrieve_examples(
            tenant_id, self._source_id, question, n_results=2
        )

        # 4. SQL generation
        sql = await self._generate_sql(
            question=question,
            schema_context=schema_context,
            few_shot_examples=few_shot,
            custom_rules=custom_rules,
            db_type=db_type,
            history_context=history_context,
            llm_model=llm_model,
            query_type=query_type,
        )
        if not sql:
            return LakeWorkerResult.failure(
                self._source_id, db_type, "SQL generation produced no valid query"
            )

        # 5. Execution
        return await self._execute(
            sql=sql,
            db_type=db_type,
            db_url=db_url,
            mcp_config=mcp_config,
        )

    # Patterns that indicate a pure visualization change with no new data needed
    _VIZ_ONLY_RE = re.compile(
        r"^\s*(give\s+me\s+a?\s*)?(pie|bar|line|scatter|area|donut|column|histogram)\s*(chart|graph|plot)?",
        re.IGNORECASE,
    )

    async def _generate_sql(
        self,
        *,
        question: str,
        schema_context: str,
        few_shot_examples: str,
        custom_rules: str,
        db_type: str,
        history_context: str,
        llm_model: str,
        query_type: str = "NEW_TOPIC",
    ) -> Optional[str]:
        from axiom.connectors.factory import ConnectorFactory

        dialect_name, dialect_rules = await ConnectorFactory.get_dialect_info(db_type)

        prompt = (
            f"You are a precise SQL expert.\n"
            f"Target database: {dialect_name.upper()}\n\n"
            f"### SCHEMA:\n{schema_context}\n\n"
            f"### BUSINESS GLOSSARY:\n{custom_rules or 'None'}\n\n"
            f"### EXAMPLES:\n{few_shot_examples or 'None'}\n\n"
            f"### HISTORY:\n{history_context or 'None'}\n\n"
            f"### DIALECT RULES:\n{dialect_rules}\n\n"
            f"Generate a SELECT query to answer: {question}\n"
            "Output ONLY the SQL inside <sql></sql> tags. "
            "SELECT only — no writes, no DDL."
        )

        # Pure visualization refinement: if the question only changes chart type and
        # there's a previous working SQL in history, reuse it directly without an LLM call.
        if query_type == "REFINEMENT" and self._VIZ_ONLY_RE.match(question):
            sql_matches = re.findall(r"SQL:\s*(SELECT.*?)\n", history_context, re.DOTALL | re.IGNORECASE)
            if sql_matches:
                logger.info(
                    "LakeWorker %s: visualization refinement — reusing previous SQL", self._source_id
                )
                return sql_matches[-1].strip()

        try:
            response = await self._client.chat.completions.create(
                model=llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            content = response.choices[0].message.content or ""
            # Strip reasoning blocks emitted by some models
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

            sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
            if sql_match:
                sql = sql_match.group(1).strip()
            else:
                sql = content.replace("```sql", "").replace("```", "").strip()

            if sql and sql.upper().startswith("SELECT"):
                return sql

            # General REFINEMENT fallback: if LLM produced nothing valid, borrow last SQL
            if query_type == "REFINEMENT":
                sql_matches = re.findall(r"SQL:\s*(SELECT.*?)\n", history_context, re.DOTALL | re.IGNORECASE)
                if sql_matches:
                    logger.info(
                        "LakeWorker %s: REFINEMENT fallback — reusing previous SQL", self._source_id
                    )
                    return sql_matches[-1].strip()

            return None
        except Exception as exc:
            logger.warning("LakeWorker %s SQL generation error: %s", self._source_id, exc)
            return None

    async def _execute(
        self,
        *,
        sql: str,
        db_type: str,
        db_url: str,
        mcp_config: dict,
    ) -> LakeWorkerResult:
        if not sql.strip().upper().startswith("SELECT"):
            return LakeWorkerResult.failure(
                self._source_id, db_type, "Non-SELECT query blocked by security policy"
            )

        try:
            from axiom.connectors.factory import ConnectorFactory

            connector = await ConnectorFactory.get_connector(
                self._source_id, db_type, db_url, mcp_config
            )
            result = await connector.execute_query(sql)
            rows = result["rows"]

            if not rows:
                return LakeWorkerResult.failure(
                    self._source_id, db_type, "ZERO_RESULTS: query returned no rows"
                )

            display_rows = rows[:100]
            sql_result_json = json.dumps(
                {
                    "columns": result["columns"],
                    "rows": display_rows,
                    "total_count": len(rows),
                },
                default=str,
            )
            return LakeWorkerResult(
                source_id=self._source_id,
                db_type=db_type,
                sql_query=sql,
                sql_result=sql_result_json,
                row_count=len(rows),
                error=None,
                duration_ms=0.0,
            )
        except Exception as exc:
            logger.warning("LakeWorker %s execution error: %s", self._source_id, exc)
            return LakeWorkerResult.failure(self._source_id, db_type, str(exc))
