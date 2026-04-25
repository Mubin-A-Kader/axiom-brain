import logging
from typing import Dict
import starlette.types
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport

from axiom.security.trust.pep import ABACPolicyEngine, PolicyEnforcementPoint

logger = logging.getLogger("mcp-hub")

class MCPHub:
    """
    Registry for MCP servers exposed over SSE via FastAPI.
    Includes Policy Enforcement Point (PEP) for Zero Trust.
    """
    def __init__(self):
        self.servers: Dict[str, Server] = {}
        # Multi-session support: Store transports by session ID
        self.transports: Dict[str, SseServerTransport] = {}
        self.router = APIRouter(prefix="/mcp")
        self.pep = PolicyEnforcementPoint(ABACPolicyEngine())
        self._setup_routes()

    def register_server(self, name: str, server: Server):
        self.servers[name] = server
        logger.info(f"Registered MCP server: {name}")

    def _setup_routes(self):
        @self.router.get("/{server_name}/sse")
        async def sse_endpoint(server_name: str, request: Request):
            if server_name not in self.servers:
                raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")
            
            agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
            logger.info(f"SSE Connection: {agent_did} -> {server_name}")
            
            server = self.servers[server_name]
            transport = SseServerTransport(f"/mcp/{server_name}/messages")
            
            async def event_generator():
                async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                    self.transports[transport.session_id] = transport
                    logger.info(f"MCP Session {transport.session_id} established for {server_name}")
                    try:
                        await server.run(read_stream, write_stream, server.create_initialization_options())
                    finally:
                        self.transports.pop(transport.session_id, None)
                        logger.info(f"MCP Session {transport.session_id} closed")

            # SseServerTransport.connect_sse returns a context manager that handles the SSE lifecycle.
            # However, FastAPI needs a Response object. 
            # We can use a custom response or follow the starlette pattern.
            
            # The most robust way to integrate mcp-python's SSE with FastAPI/Starlette:
            return StreamingResponse(
                # connect_sse handles the 'text/event-stream' headers and handshake
                # but we need to run it in the background of the response.
                # Actually, mcp-python's SseServerTransport is designed to be called 
                # inside an ASGI scope directly.
                self._handle_mcp_sse(request, server, transport),
                media_type="text/event-stream"
            )

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            session_id = request.query_params.get("sessionId")
            if not session_id or session_id not in self.transports:
                raise HTTPException(status_code=404, detail="MCP Session not found")
            
            transport = self.transports[session_id]
            
            # Zero Trust: Intercept JSON-RPC
            body = await request.json()
            if body.get("method") == "tools/call":
                agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
                params = body.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                authorized = await self.pep.authorize_tool_call(
                    agent_did, tool_name, server_name, arguments
                )
                
                if not authorized:
                    logger.warning(f"BLOCKING tool call: {agent_did} -> {tool_name} on {server_name}")
                    return {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "error": {"code": -32000, "message": "Access Denied: Policy violation"}
                    }

            return await transport.handle_post_message(request.scope, request.receive, request._send)

    async def _handle_mcp_sse(self, request, server, transport):
        """
        Bridge between MCP connect_sse and FastAPI StreamingResponse.
        """
        async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
            self.transports[transport.session_id] = transport
            
            server_task = asyncio.create_task(
                server.run(read_stream, write_stream, server.create_initialization_options())
            )
            
            try:
                # Keep the stream open as long as the server is running
                while not server_task.done():
                    await asyncio.sleep(0.1)
                    # Yielding nothing just to keep the generator alive
                    # SseServerTransport handles the actual sending via request._send
                    yield ""
                
                await server_task
            finally:
                self.transports.pop(transport.session_id, None)
                if not server_task.done():
                    server_task.cancel()

# Global Hub instance
hub = MCPHub()
import asyncio
