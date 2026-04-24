import logging
import json
import re
from typing import Any, List, Dict, Optional
import asyncpg
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class SniffResult(BaseModel):
    table: str
    column: str
    sample_value: Any
    pattern_type: str  # "exact", "eav", "json"

class DynamicSchemaMapper:
    """
    Utilities for discovering data patterns in dynamic or EAV-style schemas.
    """
    
    @staticmethod
    async def keyword_scan_tables(conn: asyncpg.Connection, keywords: List[str]) -> List[str]:
        """Scan information_schema for tables that have columns matching the keywords."""
        if not keywords: return []
        
        # Build OR condition for keywords
        conditions = " OR ".join(["column_name ILIKE $1" for _ in range(len(keywords))])
        # Add a more robust search that includes common silo terms
        search_pattern = f"%{keywords[0]}%"
        
        query = f"""
            SELECT DISTINCT table_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND ({conditions} OR table_name ILIKE $1)
            LIMIT 10
        """
        # Simplify to use the first/main keyword for the check to keep it fast
        rows = await conn.fetch(query, search_pattern)
        return [r['table_name'] for r in rows]

    @staticmethod
    async def find_similar_tables(conn: asyncpg.Connection, target: str) -> List[str]:
        """Find tables with names similar to target.

        Returns list of schema."table" strings. Prioritises exact case-insensitive
        matches so lead_lead → lead_Lead is always found before fuzzy candidates.
        """
        bare = target.split(".")[-1].strip('"').strip("'")

        # 1. Exact case-insensitive match (handles capitalisation mismatches like lead_lead → lead_Lead)
        exact_rows = await conn.fetch(
            """SELECT schemaname, tablename
               FROM pg_catalog.pg_tables
               WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                 AND lower(tablename) = lower($1)
               LIMIT 5""",
            bare,
        )
        if exact_rows:
            return [f'{r["schemaname"]}."{r["tablename"]}"' for r in exact_rows]

        # 2. Fuzzy: pg_trgm similarity if available, otherwise ILIKE substring
        try:
            has_trgm = await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
            if has_trgm:
                rows = await conn.fetch(
                    """SELECT schemaname, tablename
                       FROM pg_catalog.pg_tables
                       WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                         AND tablename % $1
                       ORDER BY similarity(tablename, $1) DESC
                       LIMIT 5""",
                    bare,
                )
                if rows:
                    return [f'{r["schemaname"]}."{r["tablename"]}"' for r in rows]
        except Exception:
            pass

        # 3. Substring ILIKE fallback
        rows = await conn.fetch(
            """SELECT schemaname, tablename
               FROM pg_catalog.pg_tables
               WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                 AND tablename ILIKE $1
               LIMIT 5""",
            f"%{bare}%",
        )
        return [f'{r["schemaname"]}."{r["tablename"]}"' for r in rows]

    @staticmethod
    async def get_searchable_columns(conn: asyncpg.Connection, schema: str = 'public') -> List[Dict[str, str]]:
        """Identify all TEXT, VARCHAR, and JSONB columns in the database."""
        query = """
            SELECT table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = $1 
            AND data_type IN ('text', 'character varying', 'jsonb')
            AND table_name NOT IN (SELECT viewname FROM pg_catalog.pg_views)
        """
        rows = await conn.fetch(query, schema)
        return [{"table": r['table_name'], "column": r['column_name'], "type": r['data_type']} for r in rows]

    @staticmethod
    async def sniff_value(conn: asyncpg.Connection, search_value: str, columns: List[Dict[str, str]]) -> List[SniffResult]:
        """Perform a multi-column ILIKE search to find where a specific value might be hiding."""
        results = []
        # Limit the number of columns we sniff to prevent performance hits
        # Prioritize columns with 'name', 'value', 'meta', 'attr' in them
        prioritized = []
        others = []
        for col in columns:
            name = col['column'].lower()
            if any(k in name for k in ['name', 'value', 'meta', 'attr', 'key', 'content']):
                prioritized.append(col)
            else:
                others.append(col)
        
        search_queue = (prioritized + others)[:20] # Top 20 candidate columns
        
        for col in search_queue:
            try:
                table = col['table']
                column = col['column']
                
                # Sniffing query
                sniff_query = f'SELECT "{column}" FROM "{table}" WHERE "{column}"::text ILIKE $1 LIMIT 1'
                val = await conn.fetchval(sniff_query, f'%{search_value}%')
                
                if val:
                    # Detect pattern
                    pattern = "exact"
                    if "key" in column.lower() or "value" in column.lower():
                        pattern = "eav"
                    elif col['type'] == 'jsonb':
                        pattern = "json"
                        
                    results.append(SniffResult(
                        table=table,
                        column=column,
                        sample_value=str(val)[:100],
                        pattern_type=pattern
                    ))
            except Exception as e:
                logger.debug(f"Sniffing failed for {col['table']}.{col['column']}: {e}")
                continue
                
        return results
