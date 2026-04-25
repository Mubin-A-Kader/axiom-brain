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
            """
            Direct ASGI handling for MCP SSE connections.
            """
            if server_name not in self.servers:
                raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")
            
            agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
            logger.info(f"SSE Connection: {agent_did} -> {server_name}")
            
            server = self.servers[server_name]
            transport = self.server_transports[server_name]
            
            # Direct ASGI integration: connect_sse handles the 'send' and 'receive'
            # of the ASGI scope, managing headers and the stream internally.
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                logger.info(f"MCP Session established for {server_name}")
                # Run the server on the established streams
                await server.run(read_stream, write_stream, server.create_initialization_options())
                logger.info(f"MCP Session closed for {server_name}")

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

            # handle_post_message routes the JSON-RPC to the correct session writer
            return await transport.handle_post_message(request.scope, request.receive, request._send)

# Global Hub instance
hub = MCPHub()
