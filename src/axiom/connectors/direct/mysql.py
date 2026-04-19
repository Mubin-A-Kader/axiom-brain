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
        """Initialize the aiomysql connection pool."""
        if not self._pool:
            conn_args = self._parse_url(self.db_url)
            self._pool = await aiomysql.create_pool(
                **conn_args,
                minsize=self.config.get("min_pool_size", 1),
                maxsize=self.config.get("max_pool_size", 10),
                autocommit=True
            )
            logger.info(f"Initialized MySQL pool for source: {self.source_id}")

    async def disconnect(self) -> None:
        """Close the aiomysql connection pool."""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info(f"Closed MySQL pool for source: {self.source_id}")

    def _serialize(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return v

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
                    
                    # Formulate DDL
                    col_defs = [f"{col['Field']} {col['Type']}" for col in columns]
                    ddl = f"CREATE TABLE {table_name} ({', '.join(col_defs)})"
                    
                    # 3. Get foreign keys (MySQL information_schema)
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
                    
                    schema[table_name] = {
                        "ddl": ddl,
                        "columns": col_names,
                        "foreign_keys": foreign_keys,
                        "description": f"Autogenerated schema for table {table_name}.",
                    }
        return schema
