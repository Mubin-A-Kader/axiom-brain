import json
import logging
from typing import Any, Dict, Optional
import urllib.parse

from axiom.connectors.base import BaseConnector, QueryMode

logger = logging.getLogger(__name__)

class N8nConnector(BaseConnector):
    """
    DuckDB-powered connector for querying the dynamic n8n HTTP proxy webhook.
    It allows the LLM to write SQL queries that pull data from the generic n8n proxy using read_json_auto().
    """
    query_mode = QueryMode.SQL

    @property
    def dialect_name(self) -> str:
        return "duckdb"

    @property
    def llm_prompt_instructions(self) -> str:
        return """
    - N8N PROXY INSTRUCTIONS (CRITICAL): The target database is an in-memory DuckDB instance. You MUST fetch data using the `fetch_api(url)` table-valued macro.
    - HOW TO USE: `SELECT * FROM fetch_api('https://api.github.com/users/torvalds')`
    - To perform multi-table JOINs across different APIs, use multiple `fetch_api` calls: `SELECT a.*, b.* FROM fetch_api('url1') a JOIN fetch_api('url2') b ON ...`
    - Only perform GET requests via SQL. For mutations, use app agent tools.
        """.strip()

    async def connect(self) -> None:
        import duckdb
        # We spawn an ephemeral in-memory database per query
        self._conn = duckdb.connect(':memory:')
        # Enable HTTP and JSON extensions
        self._conn.execute("INSTALL httpfs; LOAD httpfs;")
        self._conn.execute("INSTALL json; LOAD json;")
        
        config = self.config or {}
        webhook_secret = config.get("webhook_secret", "")
        # Create a macro that handles the webhook url and secret automatically
        # DuckDB's read_json_auto can't easily pass dynamic headers from macro params, 
        # so we pass the URL as a base64 encoded query string parameter to avoid breaking the webhook URL.
        self._conn.execute(f"CREATE MACRO fetch_api(target_url) AS TABLE SELECT * FROM read_json_auto('{self.db_url}?method=GET&target_b64=' || to_base64(target_url), headers={{'X-Axiom-Secret': '{webhook_secret}'}})")

    async def disconnect(self) -> None:
        if hasattr(self, "_conn") and self._conn:
            self._conn.close()
            self._conn = None

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        await self.connect()
        try:
            # We run the query asynchronously (in a thread pool since duckdb is synchronous)
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _run():
                cursor = self._conn.execute(sql)
                if cursor.description is None:
                    return {"columns": [], "rows": []}
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                # DuckDB returns tuples, we convert to list of lists with standard types
                return {"columns": columns, "rows": [list(row) for row in rows]}

            result = await loop.run_in_executor(None, _run)
            return result
        finally:
            await self.disconnect()

    async def get_schema(self) -> Dict[str, Any]:
        """
        Since n8n is a proxy for 7000+ APIs, we don't have a static schema.
        We provide the proxy endpoint and instructions to the LLM so it knows how to query it.
        """
        from axiom.connectors.n8n.services import get_service
        
        config = self.config or {}
        webhook_secret = config.get("webhook_secret", "")
        service_id = config.get("service_id", "unknown_service")
        
        service_desc = f"Dynamic proxy for {service_id}. Use DuckDB's read_json_auto to query REST API endpoints."
        try:
            svc = get_service(service_id)
            if svc:
                service_desc += f"\n\nAPI INSTRUCTIONS:\n{svc.description}"
        except Exception:
            pass
            
        # Build the example URL
        example_target = urllib.parse.quote_plus(f"https://api.{service_id}.com/v1/resource")
        proxy_url = f"{self.db_url}?method=GET&url={example_target}"

        ddl = f"-- This is a dynamic proxy for {service_id}.\n"
        ddl += f"-- Use DuckDB to query it:\n"
        ddl += f"-- SELECT * FROM read_json_auto('{self.db_url}?method=GET&url=<ENCODED_API_URL>', headers={{'X-Axiom-Secret': '{webhook_secret}'}})\n"
        ddl += f"-- IMPORTANT: URL encode the target API URL! (e.g. replace :// with %3A%2F%2F)\n"
        ddl += f"CREATE VIEW n8n_proxy AS SELECT 'Dynamic JSON' as data;"

        return {
            f"n8n_{service_id}_proxy": {
                "columns": ["data"],
                "foreign_keys": [],
                "ddl": ddl,
                "description": service_desc
            }
        }
