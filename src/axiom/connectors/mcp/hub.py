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
            
            agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
            server = self.servers[server_name]
            transport = self.server_transports[server_name]
            
            # Direct ASGI bridge: This is the most stable way to use MCP with FastAPI
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            # FIX: Normalize sessionId. The client sends 'session_id' but transport needs 'sessionId'
            sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
            
            # Re-construct scope with corrected sessionId for the MCP library
            from starlette.datastructures import QueryParams
            new_query = f"sessionId={sid}"
            mutable_scope = dict(request.scope)
            mutable_scope["query_string"] = new_query.encode()
            
            if server_name not in self.server_transports:
                raise HTTPException(status_code=404)
            
            transport = self.server_transports[server_name]
            
            # Zero Trust Check
            body = await request.json()
            if body.get("method") == "tools/call":
                agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
                authorized = await self.pep.authorize_tool_call(
                    agent_did, body["params"]["name"], server_name, body["params"].get("arguments", {})
                )
                if not authorized:
                    return {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000, "message": "Denied"}}

            return await transport.handle_post_message(mutable_scope, request.receive, request._send)

hub = MCPHub()
