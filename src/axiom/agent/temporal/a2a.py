import logging
import json
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException
from axiom.security.trust.ans import AgentNamingService

logger = logging.getLogger("axiom-a2a")

class A2AOrchestrator:
    """
    Implements the Agent2Agent (A2A) protocol.
    Allows agents to delegate tasks to other agents via a standardized JSON-RPC over HTTP/SSE interface.
    """
    def __init__(self):
        self.router = APIRouter(prefix="/a2a")
        self._setup_routes()
        # Registry of agent capabilities
        self._agents = {
            "sql_generator": {
                "did": "did:axiom:agent:sql_generator",
                "capabilities": ["generate_sql", "validate_sql"]
            },
            "planner": {
                "did": "did:axiom:agent:planner",
                "capabilities": ["create_execution_plan"]
            }
        }

    def _setup_routes(self):
        @self.router.post("/dispatch")
        async def dispatch_task(request: Request):
            """
            Standardized A2A Task Dispatcher.
            Expects a JSON-RPC 2.0 payload.
            """
            body = await request.json()
            method = body.get("method")
            params = body.get("params", {})
            
            # Zero Trust Verification
            caller_did = request.headers.get("X-Agent-DID")
            if not caller_did:
                raise HTTPException(status_code=401, detail="Missing X-Agent-DID")
                
            logger.info(f"A2A Dispatch: {caller_did} calling {method}")
            
            # Simple routing based on method names
            if method == "generate_sql":
                return await self._delegate_to_generator(params, caller_did)
            
            raise HTTPException(status_code=404, detail=f"A2A Method '{method}' not found")

    async def _delegate_to_generator(self, params: Dict[str, Any], caller_did: str) -> Dict[str, Any]:
        # In a real system, this would trigger a sub-workflow or another worker
        # For Phase 4, we simulate the A2A handoff
        from axiom.agent.nodes import SQLGenerationNode
        from axiom.rag.schema import SchemaRAG
        
        # Instantiate the specialized agent
        generator = SQLGenerationNode(SchemaRAG())
        
        # In A2A, the generator only sees the exact state it needs
        sub_state = {
            "question": params.get("question"),
            "schema_context": params.get("schema_context"),
            "few_shot_examples": params.get("few_shot_examples", ""),
            "tenant_id": params.get("tenant_id")
        }
        
        result = await generator(sub_state)
        
        return {
            "jsonrpc": "2.0",
            "result": result,
            "id": str(uuid.uuid4())
        }

# Global A2A Instance
a2a = A2AOrchestrator()
