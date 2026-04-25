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
    """
    ASGI 'receive' wrapper that injects a cached body back into the stream.
    This allows the MCP library to read the body even if we already consumed it for Zero Trust.
    """
    def __init__(self, real_receive, cached_body: bytes):
        self.real_receive = real_receive
        self.cached_body = cached_body
        self.sent_cached = False

    async def __call__(self):
        if not self.sent_cached:
            self.sent_cached = True
            return {
                "type": "http.request",
                "body": self.cached_body,
                "more_body": False
            }
        return await self.real_receive()

class MCPMessageResponse(Response):
    """
    Delegates response to the MCP library while providing the re-injected body.
    """
    def __init__(self, transport: SseServerTransport, scope: dict, body: bytes):
        super().__init__()
        self.transport = transport
        self.mcp_scope = scope
        self.cached_body = body

    async def __call__(self, scope, receive, send) -> None:
        # Wrap the receive stream to include our cached body
        wrapped_receive = BodyReinjectingReceive(receive, self.cached_body)
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
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            if server_name not in self.server_transports:
                raise HTTPException(status_code=404)
            
            transport = self.server_transports[server_name]
            
            # 1. Normalize session_id
            sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
            if not sid:
                raise HTTPException(status_code=400, detail="Missing session_id")
            try:
                clean_sid = uuid.UUID(sid).hex
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid session_id")

            # 2. Read and cache the body for Zero Trust
            raw_body = await request.body()
            body_json = json.loads(raw_body)
            
            # 3. Zero Trust Check
            if body_json.get("method") == "tools/call":
                agent_did = request.headers.get("X-Agent-DID", "did:axiom:unknown")
                authorized = await self.pep.authorize_tool_call(
                    agent_did, body_json["params"]["name"], server_name, body_json["params"].get("arguments", {})
                )
                if not authorized:
                    return {"jsonrpc": "2.0", "id": body_json.get("id"), "error": {"code": -32000, "message": "Denied"}}

            # 4. Prepare scope
            mutable_scope = dict(request.scope)
            mutable_scope["query_string"] = f"session_id={clean_sid}".encode()
            
            # 5. Return delegating response with cached body
            return MCPMessageResponse(transport, mutable_scope, raw_body)

hub = MCPHub()
