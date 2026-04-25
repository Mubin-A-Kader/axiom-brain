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
        from axiom.connectors.mcp.registry import mcp_registry
        import os
        
        # --- Heartbeat Implementation ---
        activity.heartbeat("Initializing schema retrieval")
        
        tenant_id = state["tenant_id"]
        source_id = state.get("source_id", "default_source")
        selected_tables = state.get("selected_tables", [])
        search_query = state["question"]
        
        from axiom.security.trust.ans import AgentNamingService
        session_did = AgentNamingService.generate_session_did(tenant_id, state.get("session_id", "default"))
        agent_did = AgentNamingService.generate_agent_did("knowledge_retrieval", session_did)

        base_url = os.environ.get("AXIOM_API_URL", "http://localhost:8080")
        knowledge_hub_url = f"{base_url}/mcp/knowledge/sse"
        
        # STABILIZATION: Create a NEW connection for every activity task
        # Caching connections across tasks in Temporal activities causes anyio scope errors
        from axiom.connectors.mcp_adapter import MCPConnector
        connector = MCPConnector(
            "knowledge_retrieval", 
            knowledge_hub_url, 
            {"headers": {"X-Agent-DID": agent_did}}
        )
        await connector.connect()
        
        try:
            if selected_tables:
                activity.heartbeat(f"Retrieving schema for {len(selected_tables)} tables")
                res = await connector._session.call_tool("retrieve_schema", arguments={
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "question": " ".join(selected_tables),
                    "n_results": 10
                })
                state["schema_context"] = res.content[0].text if res.content else "No schema found."
            else:
                activity.heartbeat("Performing vector search for schema retrieval")
                res = await connector._session.call_tool("retrieve_schema", arguments={
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "question": search_query,
                    "n_results": 5
                })
                state["schema_context"] = res.content[0].text if res.content else "No schema found."

            activity.heartbeat("Retrieving few-shot examples")
            res_ex = await connector._session.call_tool("retrieve_examples", arguments={
                "tenant_id": tenant_id,
                "source_id": source_id,
                "question": search_query,
                "n_results": 2
            })
            state["few_shot_examples"] = res_ex.content[0].text if res_ex.content else ""
        except Exception as e:
            logger.error(f"Retrieve schema activity failed: {e}")
            state["error"] = str(e)

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
        from axiom.connectors.mcp.registry import mcp_registry
        import os
        import json
        
        sql = (state["sql_query"] or "").strip()
        source_id = state.get("source_id", "default_source")
        
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
            # STABILIZATION: Create fresh connection for every task
            from axiom.connectors.mcp_adapter import MCPConnector
            connector = MCPConnector(source_id, target_db_url, config)
            await connector.connect()
            result = await connector.execute_query(sql)

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
