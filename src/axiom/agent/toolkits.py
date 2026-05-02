from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# OpenAI-compatible tool schema for the SQL critic's investigation tools.
# Defined once here; SQLCriticNode imports CRITIC_TOOLS instead of repeating the dict.
CRITIC_TOOLS: list[dict[str, Any]] = [
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
                    "table_name": {"type": "string", "description": "e.g. 'ptemplate_questions'"},
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
                    "table_name": {"type": "string"},
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


class SQLInvestigationToolkit:
    """Executes the three critic investigation tools against a live connector.

    Replaces the inline _dispatch_tool closure in SQLCriticNode with a
    testable, reusable class. The connector is passed per-call rather than
    stored so the toolkit is stateless and can be shared across requests.
    """

    async def dispatch(self, name: str, args: dict[str, Any], connector: Any) -> str:
        try:
            if name == "describe_table":
                return await self._describe_table(args, connector)
            if name == "sample_values":
                return await self._sample_values(args, connector)
            if name == "run_query":
                return await self._run_query(args, connector)
            return f"Unknown tool: {name}"
        except Exception as exc:
            return f"Tool error ({name}): {exc}"

    async def _describe_table(self, args: dict[str, Any], connector: Any) -> str:
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

    async def _sample_values(self, args: dict[str, Any], connector: Any) -> str:
        fqt = f"\"{args['schema_name']}\".\"{args['table_name']}\""
        col = args["column_name"]
        sql = (
            f"SELECT DISTINCT \"{col}\"::text "
            f"FROM {fqt} "
            f"WHERE \"{col}\" IS NOT NULL LIMIT 30"
        )
        r = await connector.execute_query(sql)
        return json.dumps([row[0] for row in r["rows"]], default=str)

    async def _run_query(self, args: dict[str, Any], connector: Any) -> str:
        raw = args.get("sql", "").strip()
        if not raw:
            return "Error: SQL is empty."
        if not isinstance(raw, str):
            raw = str(raw).strip()
        if not raw.upper().startswith("SELECT"):
            return "Blocked: only SELECT queries allowed."
        fixed = re.sub(
            r'"([^"]+)"\s+(I?LIKE)',
            r'"\1"::text \2',
            raw,
            flags=re.IGNORECASE,
        )
        r = await connector.execute_query(fixed)
        return json.dumps(r["rows"][:15], default=str)


# Module-level singleton — SQLCriticNode imports this.
investigation_toolkit = SQLInvestigationToolkit()
