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
        self.server_transports: Dict[str, SseServerTransport] = {}
        
        self.router = APIRouter(prefix="/mcp")
        self.pep = PolicyEnforcementPoint(ABACPolicyEngine())
        self._setup_routes()

    def register_server(self, name: str, server: Server):
        self.servers[name] = server
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
            
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            # Extract session ID from either format
            sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
            
            if not sid:
                logger.error("Missing session_id/sessionId in query parameters")
                raise HTTPException(status_code=400, detail="Missing session_id")
            
            if server_name not in self.server_transports:
                raise HTTPException(status_code=404, detail="Server transport not found")
            
            transport = self.server_transports[server_name]
            
            # --- CRITICAL: Normalize for the underlying library ---
            # The MCP library's handle_post_message expects sessionId in the ASGI scope.
            # We must rebuild the scope to ensure it's there and correctly named.
            from starlette.datastructures import QueryParams
            mutable_scope = dict(request.scope)
            # Re-encode query string with the EXACT key 'sessionId'
            mutable_scope["query_string"] = f"sessionId={sid}".encode()
            
            # Zero Trust Check
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

            # Pass the modified scope to the library
            return await transport.handle_post_message(mutable_scope, request.receive, request._send)

# Global Hub instance
hub = MCPHub()
