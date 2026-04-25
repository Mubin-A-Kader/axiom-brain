import logging
import uuid
import asyncio
from typing import Dict
from fastapi import APIRouter, Request, HTTPException
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
        # Transports are singletons per server type
        self.server_transports: Dict[str, SseServerTransport] = {}
        # Active sessions mapping real session IDs to their server name
        self.active_sessions: Dict[str, str] = {}
        
        self.router = APIRouter(prefix="/mcp")
        self.pep = PolicyEnforcementPoint(ABACPolicyEngine())
        self._setup_routes()

    def register_server(self, name: str, server: Server):
        self.servers[name] = server
        # Create a singleton transport for this server
        self.server_transports[name] = SseServerTransport(f"/mcp/{name}/messages")
        logger.info(f"Registered MCP server and transport: {name}")

    def _setup_routes(self):
        @self.router.get("/{server_name}/sse")
        async def sse_endpoint(server_name: str, request: Request):
            if server_name not in self.servers:
                raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")
            
            agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
            logger.info(f"SSE Connection: {agent_did} -> {server_name}")
            
            server = self.servers[server_name]
            transport = self.server_transports[server_name]
            
            return await self._handle_mcp_sse(request, server, transport, server_name)

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            # Support both camelCase and snake_case for session ID
            session_id = request.query_params.get("sessionId") or request.query_params.get("session_id")
            
            if not session_id:
                raise HTTPException(status_code=400, detail="Missing session_id parameter")
            
            if server_name not in self.server_transports:
                raise HTTPException(status_code=404, detail="Server transport not found")
            
            transport = self.server_transports[server_name]
            
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

    async def _handle_mcp_sse(self, request, server, transport, server_name):
        """
        Bridge between MCP connect_sse and FastAPI ASGI scope.
        """
        async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
            # Use getattr because session_id might be set dynamically by the transport
            sid = getattr(transport, "session_id", str(uuid.uuid4()))
            self.active_sessions[sid] = server_name
            
            server_task = asyncio.create_task(
                server.run(read_stream, write_stream, server.create_initialization_options())
            )
            
            try:
                # Keep the stream open
                while not server_task.done():
                    await asyncio.sleep(0.1)
                    yield ""
                await server_task
            finally:
                self.active_sessions.pop(sid, None)
                if not server_task.done():
                    server_task.cancel()

# Global Hub instance
hub = MCPHub()
