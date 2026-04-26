import json
import asyncio
import logging
from typing import Any, Dict, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from axiom.connectors.base import BaseConnector
from axiom.connectors.dialects import DialectRegistry

logger = logging.getLogger(__name__)

class MCPConnector(BaseConnector):
    """
    Universal adapter for Model Context Protocol (MCP) servers.
    Supports both STDIO and SSE transports.
    """
    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(source_id, db_url, config)
        self._exit_stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None
        self._headers = self.config.get("headers", {})

    @property
    def dialect_name(self) -> str:
        return "postgres" 

    @property
    def llm_prompt_instructions(self) -> str:
        return """
    - SCHEMA QUALIFICATION (STRICT): Always use the fully qualified table name as shown in the SCHEMA CONTEXT.
    - STRICT QUOTING RULE: You MUST enclose any column or table name that contains an uppercase letter in double quotes.
    - For partial text searches on string columns, ALWAYS use `ILIKE '%<text>%'` for case-insensitive search.
        """.strip()

    async def connect(self) -> None:
        if self._session:
            return

        if self.db_url.startswith("http://") or self.db_url.startswith("https://"):
            logger.info(f"Connecting to MCP SSE: {self.db_url}")
            try:
                # We enter the context manager and store it in the stack
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    sse_client(self.db_url, headers=self._headers)
                )
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await self._session.initialize()
                logger.info(f"Initialized MCP SSE session for {self.source_id}")
                return
            except Exception as e:
                logger.error(f"MCP SSE Connection failed: {str(e)}")
                await self.disconnect()
                raise e

        # STDIO Fallback
        import shutil
        command_raw = self.config.get("command")
        args = self.config.get("args", [])
        if not command_raw:
            raise ValueError(f"No command provided for MCP source {self.source_id}")
        
        command = shutil.which(command_raw) or command_raw
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=self.config.get("env")
        )
        
        read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()

    async def disconnect(self) -> None:
        """
        Safely shut down the MCP session and transport.
        """
        self._session = None
        try:
            # We use a timeout to ensure cleanup doesn't hang the worker
            await asyncio.wait_for(self._exit_stack.aclose(), timeout=2.0)
        except (asyncio.TimeoutError, RuntimeError, Exception) as e:
            # We catch RuntimeError specifically for the AnyIO 'cancel scope' issue
            # to prevent it from crashing the Temporal Worker loop.
            logger.debug(f"MCP cleanup suppressed error: {e}")
        finally:
            # Create a fresh stack for the next possible connection
            self._exit_stack = AsyncExitStack()

    async def execute_query(self, sql: str) -> Dict[str, Any]:
        if not self._session:
            await self.connect()
        
        try:
            # Call the standardized 'run_query' tool
            res = await self._session.call_tool("run_query", arguments={"sql": sql})
            
            # The result is double-JSON string (from the MCP Server TextContent)
            # or a tagged string from our Zero Trust layer.
            raw_text = res.content[0].text
            
            # If it's tagged, extract the inner JSON
            if "<untrusted_data" in raw_text:
                import re
                match = re.search(r'>\s*(\{.*?\})\s*</untrusted_data>', raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1)
            
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"MCP query failed: {e}")
            raise e

    async def get_schema(self) -> Dict[str, Any]:
        if not self._session:
            await self.connect()
        
        res = await self._session.call_tool("get_schema", arguments={})
        raw_text = res.content[0].text
        
        if "<untrusted_data" in raw_text:
             import re
             match = re.search(r'>\s*(\{.*?\})\s*</untrusted_data>', raw_text, re.DOTALL)
             if match:
                 raw_text = match.group(1)
                 
        return json.loads(raw_text)
