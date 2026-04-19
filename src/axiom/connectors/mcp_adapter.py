import json
import logging
from typing import Any, Dict, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from axiom.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

class MCPConnector(BaseConnector):
    """Universal adapter for MCP-compliant database servers."""
    
    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        """
        config should contain:
        - command: The executable command (e.g., "npx", "python")
        - args: List of arguments
        - env: Optional environment variables
        """
        super().__init__(source_id, db_url, config)
        self._exit_stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None

    async def connect(self) -> None:
        """Launch the MCP server and initialize session."""
        if self._session:
            return

        import shlex
        import shutil
        command_raw = self.config.get("command")
        args = self.config.get("args", [])

        if not command_raw:
            # Try to infer from db_url if it's an MCP command string
            if self.db_url.startswith("mcp://"):
                parts = shlex.split(self.db_url.replace("mcp://", ""))
                command = parts[0]
                args = parts[1:]
            else:
                raise ValueError("MCP connector requires 'command' in config or 'mcp://' URL.")
        else:
            parts = shlex.split(command_raw)
            command = parts[0]
            if len(parts) > 1:
                # Merge arguments from command string and the explicit args list
                args = parts[1:] + args

        # Debug logging to see exactly what is being executed
        logger.debug(f"MCP DEBUG: command_raw='{command_raw}'")
        logger.debug(f"MCP DEBUG: split command='{command}'")
        logger.debug(f"MCP DEBUG: split args={args}")

        # Verify command exists
        resolved_command = shutil.which(command)
        if not resolved_command:
            logger.error(f"MCP ERROR: Command '{command}' not found in PATH.")
            # We'll still try to run it, maybe it's an alias or in a weird place
        else:
            logger.debug(f"MCP DEBUG: Resolved command path: {resolved_command}")
            # Use the resolved path for better reliability
            command = resolved_command

        # Ensure the child process inherits the current environment (os.environ)
        # and merge it with any specific env overrides from config
        import os
        full_env = os.environ.copy()
        config_env = self.config.get("env")
        if config_env and isinstance(config_env, dict):
            full_env.update(config_env)

        params = StdioServerParameters(
            command=command,
            args=args,
            env=full_env
        )

        logger.info(f"Launching MCP server for source {self.source_id}: {command} {' '.join(args)}")
        
        # Connect to server
        try:
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            
            # Initialize
            await self._session.initialize()
            logger.info(f"Initialized MCP session for source: {self.source_id}")
        except Exception as e:
            logger.error(f"MCP Connection failed for {self.source_id}: {str(e)}")
            # Try to provide more context for common errors
            if "Connection closed" in str(e):
                raise RuntimeError(
                    f"MCP server for '{self.source_id}' failed to start or crashed immediately. "
                    f"Check if the command '{command}' is correct and all required environment variables are set. "
                    "Common cause: The package might not exist on npm or requires local installation."
                ) from e
            raise e

    async def disconnect(self) -> None:
        """Close the MCP server connection."""
        await self._exit_stack.aclose()
        self._session = None
        logger.info(f"Closed MCP session for source: {self.source_id}")

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        """Call query tool on the MCP server with mapping/discovery."""
        if not self._session:
            await self.connect()
        
        # 1. Resolve tool name
        tool_map = self.config.get("tool_map", {})
        tool_name = tool_map.get("execute_query")
        
        tools = await self._session.list_tools()
        tool_list = [t.name for t in tools.tools]

        if not tool_name:
            # Auto-discovery
            candidates = ["execute_query", "run_query", "execute_sql", "query", "sql_query"]
            for c in candidates:
                if c in tool_list:
                    tool_name = c
                    break
        
        if not tool_name or tool_name not in tool_list:
            raise ValueError(f"MCP server {self.source_id} does not support a query tool. Found: {tool_list}")

        # 2. Call tool (standardizing arguments)
        # Most MCP SQL servers expect {"sql": "..."} or {"query": "..."}
        args = {"sql": sql} if "run_query" in tool_name or "sql" in tool_name else {"query": sql}
        
        result = await self._session.call_tool(tool_name, arguments=args)
        
        if not result.content or result.isError:
            error_msg = result.content[0].text if result.content else "Unknown error"
            raise RuntimeError(f"MCP Query Failed ({tool_name}): {error_msg}")

        try:
            return json.loads(result.content[0].text)
        except Exception:
            return {"columns": ["output"], "rows": [[result.content[0].text]]}

    async def get_schema(self) -> Dict[str, Any]:
        """Fetch schema with discovery and fallback synthesis."""
        if not self._session:
            await self.connect()

        tool_map = self.config.get("tool_map", {})
        tool_name = tool_map.get("get_schema")
        
        tools = await self._session.list_tools()
        tool_list = [t.name for t in tools.tools]

        # 1. Direct get_schema support
        if not tool_name:
            if "get_schema" in tool_list:
                tool_name = "get_schema"
            elif "list_tables" in tool_list:
                # Fallback to synthesis
                return await self._synthesize_schema(tool_list)

        if tool_name and tool_name in tool_list:
            result = await self._session.call_tool(tool_name, arguments={})
            if not result.isError:
                return json.loads(result.content[0].text)

        raise ValueError(f"MCP server {self.source_id} does not support 'get_schema' or 'list_tables'. Found: {tool_list}")

    async def _synthesize_schema(self, tool_list: List[str]) -> Dict[str, Any]:
        """Synthesize a full schema by calling granular discovery tools."""
        logger.info(f"Synthesizing schema for {self.source_id} using granular tools...")
        schema = {}
        
        # 1. Get table list
        res = await self._session.call_tool("list_tables", arguments={})
        # Handle various return formats (often a list of strings or JSON)
        try:
            tables_data = json.loads(res.content[0].text)
            table_names = [t["name"] if isinstance(t, dict) else t for t in tables_data]
        except Exception:
            # Fallback for text output like "Table1, Table2"
            table_names = [t.strip() for t in res.content[0].text.split(",") if t.strip()]

        # 2. Get details for each table
        describe_tool = "describe_table" if "describe_table" in tool_list else None
        
        for table in table_names:
            columns = []
            ddl = f"CREATE TABLE {table} (...)"
            
            if describe_tool:
                try:
                    desc_res = await self._session.call_tool(describe_tool, arguments={"table_name": table})
                    # This is highly server-dependent, but we try to extract what we can
                    ddl = desc_res.content[0].text
                    # Simple heuristic for columns
                    columns = [word.strip() for word in ddl.split() if word.isidentifier()][:10]
                except Exception:
                    pass
            
            schema[table] = {
                "ddl": ddl,
                "columns": columns,
                "foreign_keys": [],
                "description": f"Table {table} retrieved via {self.source_id} MCP server."
            }
            
        return schema
