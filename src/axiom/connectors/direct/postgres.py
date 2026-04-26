import json
import logging
from typing import Any, Dict, List, Optional
from datetime import date, datetime
from decimal import Decimal

import asyncpg
from axiom.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

class PostgresConnector(BaseConnector):
    """Direct async connector for PostgreSQL using asyncpg."""
    
    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(source_id, db_url, config)
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Initialize the asyncpg connection pool, with optional SSH tunneling."""
        if not self._pool:
            # 1. Start SSH tunnel if configured
            effective_url = await self._start_ssh_tunnel()
            
            # 2. Parse and unquote components to handle hyphens/special chars
            from axiom.core.cleansing import safe_db_urlparse
            from urllib.parse import unquote
            p = safe_db_urlparse(effective_url)
            
            # Create the pool using explicit args to be more robust than just passing the URL string
            self._pool = await asyncpg.create_pool(
                user=unquote(p["username"]) if p["username"] else None,
                password=unquote(p["password"]) if p["password"] else None,
                database=unquote(p["path"].lstrip('/')) if p["path"] else None,
                host=p["hostname"],
                port=p["port"] or 5432,
                min_size=self.config.get("min_pool_size", 1),
                max_size=self.config.get("max_pool_size", 10)
            )
            logger.info(f"Initialized PostgreSQL pool for source: {self.source_id}")

    async def disconnect(self) -> None:
        """Close the asyncpg connection pool and stop SSH tunnel."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info(f"Closed PostgreSQL pool for source: {self.source_id}")
        
        # Always attempt to stop tunnel on disconnect
        await self._stop_ssh_tunnel()

    def _serialize(self, v: Any) -> Any:
        import uuid
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    @property
    def dialect_name(self) -> str:
        return "postgres"

    @property
    def llm_prompt_instructions(self) -> str:
        return """
    - SCHEMA QUALIFICATION (STRICT): Always use the fully qualified table name (e.g., "public"."users" or "auth"."sessions") as shown in the SCHEMA CONTEXT. Never assume a default schema.
    - STRICT QUOTING RULE: You MUST enclose any column or table name that contains an uppercase letter in double quotes (e.g., "packageId", "UserOrders").
    - Do NOT double-quote standard snake_case columns or lowercase table names (e.g., use user_id, not "user_id").
    - For partial text searches on string columns, ALWAYS use `ILIKE '%<text>%'` for case-insensitive search.
    - JSONB CASTING RULE: You CANNOT use ILIKE on jsonb columns directly. You MUST cast to text first: `column_name::text ILIKE '%search%'`.
    - Always wrap schema, table, and column names in double quotes (e.g., "public"."Users") if they are shown as such in the SCHEMA CONTEXT.
        """.strip()

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        """Execute a SELECT query and return formatted results."""
        if not self._pool:
            await self.connect()
        
        async with self._pool.acquire() as conn:
            # --- AUTO SEARCH PATH ---
            # Dynamically find all schemas that aren't system schemas and add them to search path
            schemas = await conn.fetch("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('information_schema', 'pg_catalog')")
            path = ", ".join([f'"{s["nspname"]}"' for s in schemas])
            await conn.execute(f"SET search_path TO {path}, public")

            # Wrap the execution in a strictly read-only transaction
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(sql)
                if not rows:
                    return {"columns": [], "rows": []}
                
                cols = list(rows[0].keys())
                data = [[self._serialize(v) for v in row.values()] for row in rows]
                return {"columns": cols, "rows": data}

    async def get_schema(self) -> Dict[str, Any]:
        """Extract table structures, columns, and foreign keys across all user schemas."""
        if not self._pool:
            await self.connect()
            
        schema = {}
        async with self._pool.acquire() as conn:
            # Diagnostic: What DB am I in?
            db_name = await conn.fetchval("SELECT current_database()")
            user_name = await conn.fetchval("SELECT current_user")
            logger.info(f"Extracting schema for source '{self.source_id}' from DB '{db_name}' as user '{user_name}'")

            # 1. Get all user tables/views/mat-views across all non-system schemas
            # Using pg_class/pg_namespace for better reliability than information_schema
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
            logger.info(f"Schema extraction found {len(tables)} objects in '{db_name}'")
            
            if not tables:
                # Fallback: List ALL schemas to see what's visible
                all_schemas = await conn.fetch("SELECT nspname FROM pg_namespace")
                logger.warning(f"No tables found! Visible schemas: {[s['nspname'] for s in all_schemas]}")

            for table_record in tables:
                schema_name = table_record['table_schema']
                table_name = table_record['table_name']
                table_type = table_record['table_type']
                
                # ALWAYS use fully qualified name to avoid ambiguity in LLM routing
                full_name = f"{schema_name}.{table_name}"
                logger.debug(f"Extracting metadata for {table_type}: {full_name}")
                
                # 2. Get columns for this table
                cols_query = """
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_schema = $1 AND table_name = $2 
                    ORDER BY ordinal_position;
                """
                columns = await conn.fetch(cols_query, schema_name, table_name)
                col_names = [col['column_name'] for col in columns]
                
                # 3. Get foreign keys first so they can be embedded in the DDL
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

                # Build DDL with quoted identifiers and embedded FOREIGN KEY constraints
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
                    "columns": col_names,
                    "foreign_keys": foreign_keys,
                    "description": f"Autogenerated schema for table {full_name}.",
                }
        return schema
