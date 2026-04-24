import json
import logging
from typing import Any, Dict, List, Optional
from datetime import date, datetime
from decimal import Decimal

import aiomysql
from axiom.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

class MySQLConnector(BaseConnector):
    """Direct async connector for MySQL using aiomysql."""
    
    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(source_id, db_url, config)
        self._pool: Optional[aiomysql.Pool] = None

    def _parse_url(self, url: str) -> Dict[str, Any]:
        """Simple MySQL URL parser."""
        # Expected: mysql://user:password@host:port/dbname
        from urllib.parse import urlparse
        p = urlparse(url)
        return {
            "host": p.hostname,
            "port": p.port or 3306,
            "user": p.username,
            "password": p.password,
            "db": p.path.lstrip('/'),
        }

    async def connect(self) -> None:
        """Initialize the aiomysql connection pool, with optional SSH tunneling."""
        if not self._pool:
            # 1. Start SSH tunnel if configured
            effective_url = await self._start_ssh_tunnel()
            
            # 2. Parse effective URL (might be rewritten to localhost)
            conn_args = self._parse_url(effective_url)
            
            self._pool = await aiomysql.create_pool(
                **conn_args,
                minsize=self.config.get("min_pool_size", 1),
                maxsize=self.config.get("max_pool_size", 10),
                autocommit=True
            )
            logger.info(f"Initialized MySQL pool for source: {self.source_id}")

    async def disconnect(self) -> None:
        """Close the aiomysql connection pool and stop SSH tunnel."""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info(f"Closed MySQL pool for source: {self.source_id}")
        
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
        return "mysql"

    @property
    def llm_prompt_instructions(self) -> str:
        return """
    - SCHEMA QUALIFICATION (STRICT): Do NOT use "public" or other schema prefixes unless they are explicitly part of the database name in the SCHEMA CONTEXT.
    - STRICT QUOTING RULE: Use backticks (`) for all identifiers (tables, columns) to avoid keyword conflicts, especially for mixed-case names.
    - For partial text searches on string columns, ALWAYS use `LIKE '%<text>%'` for case-insensitive search.
    - Always wrap schema, table, and column names in backticks if they are shown as such in the SCHEMA CONTEXT.
        """.strip()

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        """Execute a SELECT query and return formatted results."""
        if not self._pool:
            await self.connect()
        
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Wrap the execution in a strictly read-only transaction
                await cur.execute("START TRANSACTION READ ONLY")
                try:
                    await cur.execute(sql)
                    rows = await cur.fetchall()
                finally:
                    await cur.execute("ROLLBACK")

                if not rows:
                    return {"columns": [], "rows": []}
                
                cols = list(rows[0].keys())
                data = [[self._serialize(v) for v in row.values()] for row in rows]
                return {"columns": cols, "rows": data}

    async def get_schema(self) -> Dict[str, Any]:
        """Extract table structures, columns, and foreign keys from current database."""
        if not self._pool:
            await self.connect()
            
        schema = {}
        conn_args = self._parse_url(self.db_url)
        db_name = conn_args["db"]

        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 1. Get all tables
                await cur.execute(f"SHOW TABLES FROM `{db_name}`")
                tables = await cur.fetchall()
                
                for table_record in tables:
                    table_name = list(table_record.values())[0]
                    
                    # 2. Get columns
                    await cur.execute(f"DESCRIBE `{db_name}`.`{table_name}`")
                    columns = await cur.fetchall()
                    col_names = [col['Field'] for col in columns]
                    
                    # 3. Get foreign keys first so they can be embedded in the DDL
                    fk_query = """
                        SELECT
                            COLUMN_NAME,
                            REFERENCED_TABLE_NAME,
                            REFERENCED_COLUMN_NAME
                        FROM
                            information_schema.KEY_COLUMN_USAGE
                        WHERE
                            TABLE_SCHEMA = %s
                            AND TABLE_NAME = %s
                            AND REFERENCED_TABLE_NAME IS NOT NULL;
                    """
                    await cur.execute(fk_query, (db_name, table_name))
                    fks = await cur.fetchall()
                    foreign_keys = [
                        {"column": fk["COLUMN_NAME"], "references": fk["REFERENCED_TABLE_NAME"]}
                        for fk in fks
                    ]

                    # Build DDL with embedded FOREIGN KEY constraints
                    col_defs = [f"`{col['Field']}` {col['Type']}" for col in columns]
                    fk_defs = [
                        f'FOREIGN KEY (`{fk["COLUMN_NAME"]}`) REFERENCES '
                        f'`{fk["REFERENCED_TABLE_NAME"]}`(`{fk["REFERENCED_COLUMN_NAME"]}`)'
                        for fk in fks
                    ]
                    ddl = f"CREATE TABLE `{table_name}` ({', '.join(col_defs + fk_defs)})"

                    schema[table_name] = {
                        "ddl": ddl,
                        "columns": col_names,
                        "foreign_keys": foreign_keys,
                        "description": f"Autogenerated schema for table {table_name}.",
                    }
        return schema
