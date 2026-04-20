import json
import asyncio
import logging
from typing import Any, Dict, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from axiom.connectors.base import BaseConnector
from axiom.connectors.dialects import DialectRegistry

logger = logging.getLogger(__name__)

# Silence noisy MCP library logs that fill the console with parsing errors
logging.getLogger("mcp").setLevel(logging.CRITICAL)

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

        # Automatically add -y to npx for non-interactive installation
        if command == "npx" and "-y" not in args:
            args = ["-y"] + args

        # Verify command exists and get absolute path
        resolved_command = shutil.which(command)
        if resolved_command:
            logger.debug(f"MCP DEBUG: Resolved command path: {resolved_command}")
            command = resolved_command
        else:
            logger.warning(f"MCP WARNING: Command '{command}' not found in PATH.")

        # --- SURGICAL LOG SCRUBBER ---
        # Filters out JSON logs and noise, only allowing real JSON-RPC to pass to the MCP client.
        # It redirects noise to stderr so it shows up in Docker logs instead of crashing the parser.
        full_command_str = f"{command} {' '.join(args)}"
        wrapped_command = "sh"
        filter_script = "import sys; [sys.stdout.write(l) if 'jsonrpc' in l and l.strip().startswith('{') else sys.stderr.write(l) for l in sys.stdin]"
        protocol_filter = f"python3 -u -c \"{filter_script}\""
        wrapped_args = ["-c", f"{full_command_str} 2>&1 | {protocol_filter}"]

        # Ensure the child process inherits the current environment (os.environ)
        # and merge it with any specific env overrides from config
        import os
        full_env = os.environ.copy()
        # CRITICAL: Ensure system paths are in the child's PATH
        if "PATH" not in full_env:
            full_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
        
        # Snowflake specific quiet flags
        full_env["SNOWFLAKE_LOGGER_LEVEL"] = "ERROR"
        full_env["LOG_LEVEL"] = "ERROR"

        config_env = self.config.get("env")
        if config_env and isinstance(config_env, dict):
            full_env.update(config_env)

        params = StdioServerParameters(
            command=wrapped_command,
            args=wrapped_args,
            env=full_env
        )

        logger.info(f"Launching MCP server for source {self.source_id}: {full_command_str}")
        
        # Connect to server
        try:
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            
            # Initialize
            await self._session.initialize()
            
            # Small warm-up delay for cloud databases
            await asyncio.sleep(1)
            
            logger.info(f"Initialized MCP session for source: {self.source_id}")
        except Exception as e:
            logger.error(f"MCP Connection failed for {self.source_id}: {str(e)}")
            if "Connection closed" in str(e):
                raise RuntimeError(
                    f"MCP server for '{self.source_id}' failed to start. "
                    "This usually means the command is wrong or npm package is not found."
                ) from e
            raise e

    async def disconnect(self) -> None:
        """Close the MCP server connection."""
        await self._exit_stack.aclose()
        self._session = None
        logger.info(f"Closed MCP session for source: {self.source_id}")

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        """Call query tool on the MCP server with dynamic pattern-based discovery."""
        if not self._session:
            await self.connect()
        
        # 1. Dynamic Discovery: Find any tool that looks like a query tool
        tool_map = self.config.get("tool_map", {})
        tool_name = tool_map.get("execute_query")
        
        tools = await self._session.list_tools()
        tool_list = [t.name for t in tools.tools]

        if not tool_name:
            # Prioritize standard names
            candidates = ["execute_query", "run_query", "query", "execute_sql"]
            for c in candidates:
                if c in tool_list:
                    tool_name = c
                    break
            
            if not tool_name:
                # Fuzzy match: Look for anything containing query/sql
                for t in tool_list:
                    t_lower = t.lower()
                    if "query" in t_lower or "sql" in t_lower:
                        tool_name = t
                        break
        
        if not tool_name:
            raise ValueError(f"MCP server {self.source_id} does not provide a query tool. Found: {tool_list}")

        # 2. Intelligent Argument Discovery: Inspect the tool schema to see what it expects
        target_tool = next((t for t in tools.tools if t.name == tool_name), None)
        arg_key = "query" # Default
        if target_tool and hasattr(target_tool, "inputSchema") and target_tool.inputSchema:
            props = target_tool.inputSchema.get("properties", {})
            if "sql" in props:
                arg_key = "sql"
            elif "query" in props:
                arg_key = "query"
        
        args = {arg_key: sql}
        
        result = await self._session.call_tool(tool_name, arguments=args)
        
        if not result.content or result.isError:
            error_msg = result.content[0].text if result.content else "Unknown error"
            raise RuntimeError(f"MCP Query Failed ({tool_name}): {error_msg}")

        try:
            return json.loads(result.content[0].text)
        except Exception:
            # Wrap raw text output in standard format
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
                return await self._synthesize_schema(tool_list)
            # Special case for query-only servers (like Snowflake)
            elif any(x in t.lower() for t in tool_list for x in ["query", "sql"]):
                return await self._discover_schema_manually()

        if tool_name and tool_name in tool_list:
            result = await self._session.call_tool(tool_name, arguments={})
            if not result.isError:
                return json.loads(result.content[0].text)

        raise ValueError(f"MCP server {self.source_id} does not support schema discovery. Found: {tool_list}")

    async def _discover_schema_manually(self) -> Dict[str, Any]:
        """Autonomous dialect discovery: Probes the DB with various dialects until one works."""
        
        # 1. Determine probe order (heuristic based on command/url)
        probe_order = ["postgresql", "default", "mysql", "snowflake"]
        cmd_str = self.db_url.lower() + str(self.config.get("command", "")).lower()
        
        if "postgres" in cmd_str:
            probe_order = ["postgresql", "default", "mysql"]
        elif "snowflake" in cmd_str:
            probe_order = ["snowflake", "default", "postgresql"]
        elif "mysql" in cmd_str:
            probe_order = ["mysql", "default", "postgresql"]

        logger.info(f"Starting autonomous schema discovery for {self.source_id}. Probe order: {probe_order}")
        
        last_error = None
        for dialect_name in probe_order:
            dialect = DialectRegistry.get_dialect(dialect_name)
            try:
                query = dialect.get_list_tables_query()
                logger.info(f"Probing {self.source_id} with {dialect_name} query...")
                tables_res = await self.execute_query(query)
                
                table_names = self._extract_table_names_with_dialect(tables_res, dialect)
                logger.info(f"Probe {dialect_name} returned {len(table_names)} tables.")
                
                if table_names:
                    logger.info(f"Autonomous discovery successful! Identified dialect: {dialect_name}")
                    return self._build_schema_from_names(table_names)
            except Exception as e:
                logger.debug(f"Probe failed for {dialect_name}: {str(e).splitlines()[0]}")
                last_error = e
                continue

        raise ValueError(f"Autonomous schema discovery failed for {self.source_id}. Last error: {last_error}")

    def _extract_table_names_with_dialect(self, tables_res: Dict[str, Any], dialect: Any) -> List[str]:
        """Extract table names using dialect hints and common fallbacks."""
        table_names = []
        if "rows" in tables_res:
            target_col = dialect.get_table_name_column()
            name_idx = 0
            if "columns" in tables_res:
                for i, col in enumerate(tables_res["columns"]):
                    if col.lower() in [target_col.lower(), "name", "table_name", "tablename"]:
                        name_idx = i
                        break
            
            for row in tables_res["rows"]:
                if row and len(row) > name_idx:
                    val = row[name_idx]
                    if val: table_names.append(str(val))
        return table_names

    def _build_schema_from_names(self, table_names: List[str]) -> Dict[str, Any]:
        """Convert a list of table names into a minimal schema object."""
        schema = {}
        for table in table_names:
            schema[table] = {
                "ddl": f"SELECT * FROM {table} LIMIT 10;",
                "columns": [],
                "foreign_keys": [],
                "description": f"Table: {table} (Autodiscovered)"
            }
        return schema

    async def _synthesize_schema(self, tool_list: List[str]) -> Dict[str, Any]:
        """Synthesize a full schema by calling granular discovery tools."""
        logger.info(f"Synthesizing schema for {self.source_id} using granular tools...")
        schema = {}
        res = await self._session.call_tool("list_tables", arguments={})
        
        try:
            tables_data = json.loads(res.content[0].text)
            table_names = [t["name"] if isinstance(t, dict) else t for t in tables_data]
        except Exception:
            table_names = [t.strip() for t in res.content[0].text.split(",") if t.strip()]

        describe_tool = "describe_table" if "describe_table" in tool_list else None
        
        for table in table_names:
            columns = []
            ddl = f"CREATE TABLE {table} (...)"
            if describe_tool:
                try:
                    desc_res = await self._session.call_tool(describe_tool, arguments={"table_name": table})
                    ddl = desc_res.content[0].text
                    columns = [word.strip() for word in ddl.split() if word.isidentifier()][:10]
                except Exception:
                    pass
            
            schema[table] = {
                "ddl": ddl,
                "columns": columns,
                "foreign_keys": [],
                "description": f"Table {table}"
            }
        return schema
