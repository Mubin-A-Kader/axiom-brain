import logging
import uuid
import json
from typing import Dict
from fastapi import APIRouter, Request, HTTPException
from starlette.responses import Response
from mcp.server import Server
from mcp.server.sse import SseServerTransport

from axiom.security.trust.pep import ABACPolicyEngine, PolicyEnforcementPoint

logger = logging.getLogger("mcp-hub")

class BodyReinjectingReceive:
    def __init__(self, real_receive, cached_body: bytes):
        self.real_receive = real_receive
        self.cached_body = cached_body
        self.sent_cached = False

    async def __call__(self):
        if not self.sent_cached:
            self.sent_cached = True
            return {"type": "http.request", "body": self.cached_body, "more_body": False}
        return await self.real_receive()

class MCPMessageResponse(Response):
    def __init__(self, transport: SseServerTransport, scope: dict, body: bytes):
        super().__init__(status_code=202)
        self.transport = transport
        self.mcp_scope = scope
        self.cached_body = body

    async def __call__(self, scope, receive, send) -> None:
        wrapped_receive = BodyReinjectingReceive(receive, self.cached_body)
        # We pass our specialized scope and the wrapped receive directly to the library
        await self.transport.handle_post_message(self.mcp_scope, wrapped_receive, send)

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
                raise HTTPException(status_code=404)
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
            
            # Normalize session_id from query params
            sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
            if not sid:
                raise HTTPException(status_code=400, detail="session_id required")
            
            # Read body for Zero Trust
            raw_body = await request.body()
            
            # Rebuild scope with explicit session_id for the library
            mutable_scope = dict(request.scope)
            # The library strictly parses 'session_id' from the query_string
            mutable_scope["query_string"] = f"session_id={sid}".encode()
            
            # Zero Trust Logic
            try:
                body_json = json.loads(raw_body)
                if body_json.get("method") == "tools/call":
                    agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
                    authorized = await self.pep.authorize_tool_call(
                        agent_did, body_json["params"]["name"], server_name, body_json["params"].get("arguments", {})
                    )
                    if not authorized:
                        return {"jsonrpc": "2.0", "id": body_json.get("id"), "error": {"code": -32000, "message": "Denied"}}
            except Exception:
                pass # Non-JSON or non-tool call payloads are allowed through

            return MCPMessageResponse(transport, mutable_scope, raw_body)

hub = MCPHub()
