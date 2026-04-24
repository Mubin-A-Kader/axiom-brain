import logging
from typing import Dict
import starlette.types
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
        # Store transports to handle messages correctly
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
            
            # Zero Trust: Check Agent DID in headers
            agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
            logger.info(f"SSE Connection request from {agent_did} to {server_name}")
            
            server = self.servers[server_name]
            transport = SseServerTransport(f"/mcp/{server_name}/messages")
            self.transports[server_name] = transport
            
            return await transport.handle_sse(request.scope, request.receive, request._send)

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            if server_name not in self.transports:
                raise HTTPException(status_code=404, detail="Session not found")
            
            # Zero Trust: Intercept JSON-RPC to enforce policies via PEP
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
                        "error": {
                            "code": -32000,
                            "message": f"Access Denied: Policy violation for tool '{tool_name}'"
                        }
                    }

            transport = self.transports[server_name]
            return await transport.handle_post_message(request.scope, request.receive, request._send)

# Global Hub instance
hub = MCPHub()
