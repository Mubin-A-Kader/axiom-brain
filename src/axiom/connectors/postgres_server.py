"""MCP server — PostgreSQL connector. Run as a subprocess."""
import asyncio
import json
import os
from decimal import Decimal

import psycopg2
from mcp.server import Server
from mcp.types import Tool, TextContent


def _serialize(value):
    if isinstance(value, Decimal):
        return float(value)
    return value

server = Server("postgres-connector")
_conn = psycopg2.connect(os.environ["DATABASE_URL"])


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_query",
            description="Execute a read-only SQL SELECT on PostgreSQL",
            inputSchema={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        ),
        Tool(
            name="get_schema",
            description="Return column metadata for all public tables",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cursor = _conn.cursor()

    if name == "run_query":
        sql = arguments["sql"].strip()
        if not sql.upper().startswith("SELECT"):
            return [TextContent(type="text", text="ERROR: only SELECT allowed")]
        cursor.execute(sql)
        cols = [d[0] for d in cursor.description]
        rows = [[_serialize(v) for v in row] for row in cursor.fetchall()]
        return [TextContent(type="text", text=json.dumps({"columns": cols, "rows": rows}))]

    if name == "get_schema":
        cursor.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        return [TextContent(type="text", text=json.dumps(cursor.fetchall()))]

    return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]


if __name__ == "__main__":
    from mcp.server.stdio import stdio_server

    async def _main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_main())
