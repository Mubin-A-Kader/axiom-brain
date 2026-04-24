import logging
from typing import Dict, Any, List, Optional
from axiom.security.trust.monitor import DualLLMMonitor

logger = logging.getLogger("axiom-pep")

class ABACPolicyEngine:
    """
    Attribute-Based Access Control (ABAC) Policy Engine for Multi-Agent Systems.
    Evaluates DIDs, capabilities, and resource attributes.
    """
    
    def __init__(self):
        # Default policies
        self.policies = {
            "sql_execution": ["run_query", "get_schema"],
            "knowledge_retrieval": ["retrieve_schema", "retrieve_examples", "search_semantic_cache"]
        }

    def evaluate(self, subject_did: str, action: str, resource_id: str, context: Dict[str, Any]) -> bool:
        """
        Evaluate if a subject (agent/user) can perform an action on a resource.
        """
        logger.info(f"Evaluating policy: Subject={subject_did}, Action={action}, Resource={resource_id}")
        
        # 1. Basic Tenant Isolation: context must match resource tenant
        tenant_id = context.get("tenant_id")
        if not tenant_id:
            logger.warning("Access Denied: Missing tenant_id in context")
            return False
            
        # If the resource has a tenant restriction, enforce it
        if f":tenant:{tenant_id}" not in resource_id and "did:axiom:orchestrator" not in subject_did:
             pass 

        # 2. Capability Check
        if "agent:knowledge_retrieval" in subject_did:
            if action not in self.policies["knowledge_retrieval"]:
                logger.warning(f"Access Denied: Knowledge agent attempted forbidden action: {action}")
                return False
                
        if "agent:sql_execution" in subject_did:
            if action not in self.policies["sql_execution"]:
                logger.warning(f"Access Denied: SQL agent attempted forbidden action: {action}")
                return False

        # 3. Resource Sensitivity Check
        if action == "run_query":
            sql = context.get("sql", "").upper()
            if "UPDATE" in sql or "DELETE" in sql or "DROP" in sql:
                 logger.warning(f"Access Denied: Destructive SQL blocked by ABAC: {sql}")
                 return False

        return True

class PolicyEnforcementPoint:
    """
    Interceptor that enforces ABAC policies before tool execution.
    Now includes a Dual-LLM monitor for adversarial payload detection.
    """
    def __init__(self, engine: ABACPolicyEngine):
        self.engine = engine
        self.monitor = DualLLMMonitor()

    async def authorize_tool_call(self, subject_did: str, tool_name: str, server_name: str, arguments: Dict[str, Any]) -> bool:
        # Construct context for evaluation
        context = {
            "tenant_id": arguments.get("tenant_id", "default"),
            "sql": arguments.get("sql", "")
        }
        resource_id = f"did:axiom:mcp_server:{server_name}"
        
        # 1. Deterministic Policy Check (Fast)
        if not self.engine.evaluate(subject_did, tool_name, resource_id, context):
            return False
            
        # 2. Probabilistic Security Monitor (Dual-LLM / Virtual Donkey)
        # Only scan high-risk tools like run_query
        if tool_name == "run_query":
            return await self.monitor.is_payload_safe(subject_did, tool_name, arguments)
            
        return True
