"""
N8nConnector — treats an n8n webhook as a data source.
Axiom calls the webhook with the user's question; n8n fetches from whatever
third-party service it's wired to and returns rows as JSON.
No SQL generated — this connector lives in QueryMode.PIPELINE.
"""
import logging
from typing import Any, Dict, Optional

import httpx

from axiom.connectors.base import BaseConnector, QueryMode

logger = logging.getLogger(__name__)


class N8nConnector(BaseConnector):
    """
    Connector backed by an n8n webhook.
    db_url  = the webhook URL (e.g. https://n8n.company.com/webhook/abc123)
    config  = {"webhook_secret": "...", "source_label": "Google Sheets / Sales"}
    """

    query_mode = QueryMode.PIPELINE

    @property
    def dialect_name(self) -> str:
        return "n8n"

    @property
    def llm_prompt_instructions(self) -> str:
        return (
            "This data source is a third-party integration via n8n. "
            "The data is returned as JSON rows. "
            "Identify the relevant fields from the schema and filter logically. "
            "Do NOT generate SQL. Output a JSON filter object inside <query></query> tags with keys: "
            "'filters' (key/value pairs to match), 'limit' (int, default 100), 'fields' (list of field names to return, empty = all). "
            "Example: <query>{\"filters\": {\"status\": \"active\"}, \"limit\": 50, \"fields\": [\"name\", \"email\"]}</query>"
        )

    def build_query_prompt(
        self,
        question: str,
        schema_context: str,
        custom_rules: str,
        few_shot_examples: str,
        history_context: str,
    ) -> str:
        return (
            f"You are a data analyst querying a third-party data source via n8n.\n"
            f"Source type: {self.config.get('source_label', 'n8n integration')}\n\n"
            f"### SCHEMA:\n{schema_context}\n\n"
            f"### BUSINESS RULES:\n{custom_rules or 'None'}\n\n"
            f"### HISTORY:\n{history_context or 'None'}\n\n"
            f"### INSTRUCTIONS:\n{self.llm_prompt_instructions}\n\n"
            f"Answer this question: {question}"
        )

    def extract_query(self, llm_content: str) -> Optional[str]:
        import re
        import json
        match = re.search(r"<query>(.*?)</query>", llm_content, re.DOTALL)
        if not match:
            return "{}"
        try:
            json.loads(match.group(1).strip())
            return match.group(1).strip()
        except json.JSONDecodeError:
            return "{}"

    def is_read_only_query(self, query: str) -> bool:
        return True  # n8n webhooks are always read-only from Axiom's side

    async def connect(self) -> None:
        pass  # stateless HTTP — no persistent connection

    async def disconnect(self) -> None:
        pass

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """
        POST the filter query to the n8n webhook; return {"columns": [...], "rows": [...]}.
        query is a JSON string produced by extract_query().
        """
        import json
        webhook_url = self.db_url
        secret = self.config.get("webhook_secret", "")

        try:
            payload = json.loads(query) if query else {}
        except json.JSONDecodeError:
            payload = {}

        headers = {"X-Axiom-Secret": secret, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(webhook_url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()

        # n8n returns a list of objects — normalize to columns/rows format
        if isinstance(data, list) and data:
            columns = list(data[0].keys())
            rows = [[row.get(col) for col in columns] for row in data]
            return {"columns": columns, "rows": rows}

        if isinstance(data, dict) and "columns" in data and "rows" in data:
            return data

        return {"columns": [], "rows": []}

    async def get_schema(self) -> Dict[str, Any]:
        """
        Probe the webhook with an empty query to get a sample row,
        then infer schema from field names and value types.
        """
        webhook_url = self.db_url
        secret = self.config.get("webhook_secret", "")
        headers = {"X-Axiom-Secret": secret, "Content-Type": "application/json"}

        probe_payload: dict = {"limit": 1, "filters": {}, "fields": []}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    webhook_url,
                    json=probe_payload,
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning("N8nConnector.get_schema probe failed for %s: %s", self.source_id, e)
            source_label = self.config.get("source_label", "n8n_source")
            return _placeholder_schema(source_label)

        rows = data if isinstance(data, list) else data.get("rows", [])
        source_label = self.config.get("source_label", "n8n_source")

        if not rows:
            return _placeholder_schema(source_label)

        sample = rows[0] if isinstance(rows[0], dict) else {}
        if not sample:
            return _placeholder_schema(source_label)

        fields = {k: _infer_type(v) for k, v in sample.items()}
        ddl = _build_ddl(source_label, fields)
        return {
            "tables": {
                source_label: {
                    "columns": fields,
                    "primary_key": None,
                    "foreign_keys": [],
                    "ddl": ddl,
                }
            }
        }


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "text"


def _build_ddl(table_name: str, fields: dict) -> str:
    cols = ",\n  ".join(f'"{col}" {typ.upper()}' for col, typ in fields.items()) or '"id" TEXT'
    return f'CREATE TABLE "{table_name}" (\n  {cols}\n);'


def _placeholder_schema(source_label: str) -> dict:
    """
    Returned when the webhook probe can't fetch a sample row yet
    (e.g. the n8n workflow hasn't been triggered or the sheet is empty).
    The DDL is a minimal placeholder so the onboarding pipeline doesn't crash.
    Schema will be re-synced once real data flows through.
    """
    ddl = f'CREATE TABLE "{source_label}" (\n  "id" TEXT,\n  "value" TEXT\n);'
    return {
        "tables": {
            source_label: {
                "columns": {"id": "text", "value": "text"},
                "primary_key": None,
                "foreign_keys": [],
                "ddl": ddl,
            }
        }
    }
