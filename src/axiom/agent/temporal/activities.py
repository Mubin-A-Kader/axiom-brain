import logging
from temporalio import activity
from axiom.agent.state import SQLAgentState
from axiom.agent.nodes import SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager

logger = logging.getLogger("axiom-activities")

class SQLActivities:
    def __init__(self, rag: SchemaRAG, thread_mgr: ThreadManager):
        self.rag = rag
        self.thread_mgr = thread_mgr
        # Wrap existing nodes to be used as activities
        self.schema_node = SchemaRetrievalNode(rag)
        self.gen_node = SQLGenerationNode(rag)
        self.exec_node = SQLExecutionNode(thread_mgr, rag)

    @activity.defn
    async def retrieve_schema(self, state: SQLAgentState) -> SQLAgentState:
        update = await self.schema_node(state)
        state.update(update)
        return state

    @activity.defn
    async def plan_query(self, state: SQLAgentState) -> SQLAgentState:
        # For now, we use a pass-through or a simplified version of the planning logic
        # In a real system, this would be a separate node call
        return state

    @activity.defn
    async def generate_sql(self, state: SQLAgentState) -> SQLAgentState:
        update = await self.gen_node(state)
        state.update(update)
        return state

    @activity.defn
    async def execute_sql(self, state: SQLAgentState) -> SQLAgentState:
        # --- Phase 5: Hardware-Level Sandboxing (Simulation) ---
        from axiom.agent.temporal.sandbox import SandboxedMCPServer
        import os
        import json
        
        sql = (state["sql_query"] or "").strip()
        source_id = state.get("source_id", "default_source")
        
        # We need the connection details (normally fetched in the node, 
        # but here we force sandbox execution for production-grade isolation)
        base_url = os.environ.get("AXIOM_API_URL", "http://localhost:8080")
        target_db_url = f"{base_url}/mcp/postgres/sse"
        
        # Generate Agent DID for the sandbox session
        from axiom.security.trust.ans import AgentNamingService
        session_did = AgentNamingService.generate_session_did(state.get("tenant_id", "default"), state.get("session_id", "default"))
        agent_did = AgentNamingService.generate_agent_did("sql_execution_sandbox", session_did)
        
        config = {
            "command": None, 
            "args": [],
            "headers": {"X-Agent-DID": agent_did}
        }
        
        try:
            result = await SandboxedMCPServer.run_in_sandbox(source_id, target_db_url, config, sql)
            
            all_rows = result["rows"]
            is_truncated = len(all_rows) > 100
            display_rows = all_rows[:100] if is_truncated else all_rows
            
            state["sql_result"] = json.dumps({
                "columns": result["columns"],
                "rows": display_rows,
                "is_truncated": is_truncated,
                "total_count": len(all_rows)
            }, default=str)
            state["error"] = None
        except Exception as exc:
            state["sql_result"] = None
            state["error"] = str(exc)
            
        return state
