import logging
import uuid
from typing import Dict
from fastapi import APIRouter, Request, HTTPException
from starlette.responses import Response
from mcp.server import Server
from mcp.server.sse import SseServerTransport

from axiom.security.trust.pep import ABACPolicyEngine, PolicyEnforcementPoint

logger = logging.getLogger("mcp-hub")

class MCPMessageResponse(Response):
    """
    Custom Starlette response that delegates execution to the MCP library's ASGI handler.
    This prevents FastAPI from sending its own headers after the library has already sent them.
    """
    def __init__(self, transport: SseServerTransport, scope: dict):
        super().__init__()
        self.transport = transport
        self.mcp_scope = scope

    async def __call__(self, scope, receive, send) -> None:
        # We delegate entirely to the transport's handle_post_message
        # using the normalized scope we prepared.
        await self.transport.handle_post_message(self.mcp_scope, receive, send)

class MCPHub:
    """
    Registry for MCP servers exposed over SSE via FastAPI.
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
        logger.info(f"Registered MCP server: {name}")

    def _setup_routes(self):
        @self.router.get("/{server_name}/sse")
        async def sse_endpoint(server_name: str, request: Request):
            if server_name not in self.servers:
                raise HTTPException(status_code=404, detail="Server not found")
            
            server = self.servers[server_name]
            transport = self.server_transports[server_name]
            
            # Direct ASGI bridge
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            if server_name not in self.server_transports:
                raise HTTPException(status_code=404)
            
            transport = self.server_transports[server_name]
            
            # 1. Normalize session_id
            # The library expects 'session_id' as a valid hex UUID.
            sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
            if not sid:
                raise HTTPException(status_code=400, detail="session_id required")
            
            # Ensure it is a valid hex UUID for the library's internal UUID(hex=...) call
            try:
                # If it's already a clean hex, this passes. If not, it converts.
                clean_sid = uuid.UUID(sid).hex
            except ValueError:
                logger.error(f"Invalid UUID format for session: {sid}")
                raise HTTPException(status_code=400, detail="Invalid session_id format")

            # 2. Rebuild scope with exactly what the library expects
            mutable_scope = dict(request.scope)
            mutable_scope["query_string"] = f"session_id={clean_sid}".encode()
            
            # 3. Zero Trust
            body = await request.json()
            if body.get("method") == "tools/call":
                agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
                authorized = await self.pep.authorize_tool_call(
                    agent_did, body["params"]["name"], server_name, body["params"].get("arguments", {})
                )
                if not authorized:
                    return {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000, "message": "Denied"}}

            # 4. Return our delegating response to avoid RuntimeError/Double-headers
            return MCPMessageResponse(transport, mutable_scope)

hub = MCPHub()
