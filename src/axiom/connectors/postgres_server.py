"""
Production-grade MCP Server for PostgreSQL.
Provides tools for executing read-only queries and extracting comprehensive schema metadata.
"""
import asyncio
import json
import os
import logging
from typing import Any, Dict, List, Optional
from decimal import Decimal
from datetime import date, datetime

import asyncpg
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from mcp.server.stdio import stdio_server

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mcp-postgres-server")

class PostgresMCPServer:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.server = Server("axiom-postgres-retrieval")
        self._pool: Optional[asyncpg.Pool] = None
        self._setup_handlers()

    async def connect(self):
        """Initialize the asyncpg connection pool."""
        if not self._pool:
            self._pool = await asyncpg.create_pool(
                self.db_url,
                min_size=1,
                max_size=10
            )
            logger.info("PostgreSQL connection pool initialized.")

    async def disconnect(self):
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed.")

    def _serialize(self, v: Any) -> Any:
        import uuid
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    def _setup_handlers(self):
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [
                Tool(
                    name="run_query",
                    description="Execute a read-only SQL SELECT query on the PostgreSQL database.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sql": {"type": "string", "description": "The SQL SELECT statement to execute."}
                        },
                        "required": ["sql"],
                    },
                ),
                Tool(
                    name="get_schema",
                    description="Extract comprehensive schema metadata including tables, columns, and foreign keys.",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[TextContent]:
            if not self._pool:
                await self.connect()

            async with self._pool.acquire() as conn:
                if name == "run_query":
                    return await self._handle_run_query(conn, arguments)
                elif name == "get_schema":
                    return await self._handle_get_schema(conn)
                else:
                    return [TextContent(type="text", text=f"ERROR: Unknown tool '{name}'")]

    async def _handle_run_query(self, conn: asyncpg.Connection, arguments: dict) -> List[TextContent]:
        sql = arguments.get("sql", "").strip()
        if not sql:
            return [TextContent(type="text", text="ERROR: Missing 'sql' argument.")]
        
        # Security: Basic read-only enforcement
        if not sql.upper().startswith("SELECT") and not sql.upper().startswith("WITH"):
            return [TextContent(type="text", text="ERROR: Only SELECT queries are permitted.")]

        try:
            # Set search path to include all non-system schemas
            schemas = await conn.fetch("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('information_schema', 'pg_catalog')")
            path = ", ".join([f'"{s["nspname"]}"' for s in schemas])
            await conn.execute(f"SET search_path TO {path}, public")

            async with conn.transaction(readonly=True):
                rows = await conn.fetch(sql)
                if not rows:
                    return [TextContent(type="text", text=json.dumps({"columns": [], "rows": []}))]
                
                cols = list(rows[0].keys())
                data = [[self._serialize(v) for v in row.values()] for row in rows]
                
                # --- Phase 2: Zero Trust Tagging ---
                from axiom.security.trust.tagging import LLMTagging
                serialized_res = json.dumps({"columns": cols, "rows": data})
                tagged_res = LLMTagging.wrap_query_result(serialized_res)
                
                return [TextContent(type="text", text=tagged_res)]
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return [TextContent(type="text", text=f"ERROR: {str(e)}")]

    async def _handle_get_schema(self, conn: asyncpg.Connection) -> List[TextContent]:
        try:
            schema = {}
            # 1. Get all user tables/views across non-system schemas
            tables_query = """
                SELECT n.nspname as table_schema, c.relname as table_name,
                       CASE c.relkind 
                         WHEN 'r' THEN 'BASE TABLE' 
                         WHEN 'v' THEN 'VIEW' 
                         WHEN 'm' THEN 'MATERIALIZED VIEW' 
                         WHEN 'f' THEN 'FOREIGN TABLE'
                       END as table_type
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
                AND c.relkind IN ('r', 'v', 'm', 'f');
            """
            tables = await conn.fetch(tables_query)

            for table_record in tables:
                schema_name = table_record['table_schema']
                table_name = table_record['table_name']
                full_name = f"{schema_name}.{table_name}"
                
                # 2. Get columns
                cols_query = """
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_schema = $1 AND table_name = $2 
                    ORDER BY ordinal_position;
                """
                columns = await conn.fetch(cols_query, schema_name, table_name)
                
                # 3. Get foreign keys
                fk_query = """
                    SELECT
                        kcu.column_name,
                        ccu.table_schema AS foreign_table_schema,
                        ccu.table_name AS foreign_table_name,
                        ccu.column_name AS foreign_column_name
                    FROM
                        information_schema.table_constraints AS tc
                        JOIN information_schema.key_column_usage AS kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.constraint_column_usage AS ccu
                          ON ccu.constraint_name = tc.constraint_name
                          AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = $1 AND tc.table_name = $2;
                """
                fks = await conn.fetch(fk_query, schema_name, table_name)
                
                foreign_keys = []
                for fk in fks:
                    ref_name = f"{fk['foreign_table_schema']}.{fk['foreign_table_name']}"
                    foreign_keys.append({"column": fk["column_name"], "references": ref_name})

                # Build DDL
                quoted_full_name = f'"{schema_name}"."{table_name}"'
                col_defs = [f'"{col["column_name"]}" {col["data_type"]}' for col in columns]
                fk_defs = [
                    f'FOREIGN KEY ("{fk["column_name"]}") REFERENCES '
                    f'"{fk["foreign_table_schema"]}"."{fk["foreign_table_name"]}"("{fk["foreign_column_name"]}")'
                    for fk in fks
                ]
                ddl = f"CREATE TABLE {quoted_full_name} ({', '.join(col_defs + fk_defs)})"

                schema[full_name] = {
                    "ddl": ddl,
                    "columns": [col['column_name'] for col in columns],
                    "foreign_keys": foreign_keys,
                    "description": f"Autogenerated schema for {table_record['table_type']} {full_name}.",
                }

            # --- Phase 2: Zero Trust Tagging ---
            from axiom.security.trust.tagging import LLMTagging
            serialized_schema = json.dumps(schema)
            tagged_schema = LLMTagging.wrap_schema(serialized_schema)

            return [TextContent(type="text", text=tagged_schema)]
        except Exception as e:
            logger.error(f"Schema extraction failed: {e}")
            return [TextContent(type="text", text=f"ERROR: {str(e)}")]

    def get_server(self) -> Server:
        return self.server

    async def run(self):
        """Run the MCP server using STDIO transport."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )

if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable is required.")
        exit(1)
    
    mcp_server = PostgresMCPServer(db_url)
    try:
        asyncio.run(mcp_server.run())
    except KeyboardInterrupt:
        pass
