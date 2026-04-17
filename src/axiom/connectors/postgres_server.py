"""MCP server — PostgreSQL connector. Run as a subprocess."""
import asyncio
import os

import psycopg2
from mcp.server import Server
from mcp.types import Tool, TextContent

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
        rows = cursor.fetchall()
        return [TextContent(type="text", text=str({"columns": cols, "rows": rows}))]

    if name == "get_schema":
        cursor.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        return [TextContent(type="text", text=str(cursor.fetchall()))]

    return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]


if __name__ == "__main__":
    asyncio.run(server.run())
