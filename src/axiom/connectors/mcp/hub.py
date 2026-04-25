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
        # Multi-session support: Store transports by session ID
        # Since mcp-python's SseServerTransport is a factory, 
        # we store the writers for specific sessions here.
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
            
            # Generate a unique session ID for this specific connection
            session_id = str(uuid.uuid4())
            
            # Create a transport instance for this session
            # Note: The endpoint here should include the sessionId for mcp-python to route correctly
            transport = SseServerTransport(f"/mcp/{server_name}/messages")
            
            # connect_sse handles the entire ASGI lifecycle (headers, body, send)
            # We don't return a StreamingResponse because connect_sse calls request._send directly.
            async with transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                # The session_id on the transport object is set internally by connect_sse
                # after the handshake. Let's find it or use our generated one.
                real_session_id = getattr(transport, "session_id", session_id)
                self.transports[real_session_id] = transport
                logger.info(f"MCP Session {real_session_id} active for {server_name}")
                
                try:
                    await server.run(read_stream, write_stream, server.create_initialization_options())
                finally:
                    self.transports.pop(real_session_id, None)
                    logger.info(f"MCP Session {real_session_id} terminated")

        @self.router.post("/{server_name}/messages")
        async def handle_message(server_name: str, request: Request):
            session_id = request.query_params.get("sessionId")
            if not session_id or session_id not in self.transports:
                # If not found, try to see if it's in the body or elsewhere
                # or log available sessions for debugging
                logger.warning(f"Session {session_id} not found in {list(self.transports.keys())}")
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

# Global Hub instance
hub = MCPHub()
