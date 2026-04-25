import logging
import os
import json
import asyncio
from temporalio import activity
from axiom.agent.state import SQLAgentState
from axiom.agent.nodes import SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager
from axiom.connectors.mcp_adapter import MCPConnector
from axiom.security.trust.ans import AgentNamingService

logger = logging.getLogger("axiom-activities")

class SQLActivities:
    def __init__(self, rag: SchemaRAG, thread_mgr: ThreadManager):
        self.rag = rag
        self.thread_mgr = thread_mgr
        self.schema_node = SchemaRetrievalNode(rag)
        self.gen_node = SQLGenerationNode(rag)
        self.exec_node = SQLExecutionNode(thread_mgr, rag)

    @activity.defn
    async def retrieve_schema(self, state: SQLAgentState) -> SQLAgentState:
        tenant_id = state["tenant_id"]
        source_id = state.get("source_id", "default_source")
        selected_tables = state.get("selected_tables", [])
        
        session_did = AgentNamingService.generate_session_did(tenant_id, state.get("session_id", "default"))
        agent_did = AgentNamingService.generate_agent_did("knowledge_retrieval", session_did)

        base_url = os.environ.get("AXIOM_API_URL", "http://localhost:8080")
        knowledge_hub_url = f"{base_url}/mcp/knowledge/sse"
        
        # Isolated connection to prevent AnyIO cross-task context leaks
        connector = MCPConnector(
            "knowledge_retrieval", 
            knowledge_hub_url, 
            {"headers": {"X-Agent-DID": agent_did}}
        )
        
        try:
            activity.heartbeat("Connecting...")
            await connector.connect()
            
            activity.heartbeat("Fetching DDLs...")
            if selected_tables:
                res = await connector._session.call_tool("retrieve_schema", arguments={
                    "tenant_id": tenant_id, "source_id": source_id,
                    "question": " ".join(selected_tables), "n_results": 10
                })
            else:
                res = await connector._session.call_tool("retrieve_schema", arguments={
                    "tenant_id": tenant_id, "source_id": source_id,
                    "question": state["question"], "n_results": 5
                })
            
            state["schema_context"] = res.content[0].text if res.content else ""
            
            activity.heartbeat("Fetching Examples...")
            res_ex = await connector._session.call_tool("retrieve_examples", arguments={
                "tenant_id": tenant_id, "source_id": source_id,
                "question": state["question"], "n_results": 2
            })
            state["few_shot_examples"] = res_ex.content[0].text if res_ex.content else ""
            
        except asyncio.CancelledError:
            logger.warning("Activity retrieve_schema cancelled")
            raise
        except Exception as e:
            logger.error(f"Activity retrieve_schema failed: {e}")
            state["error"] = str(e)
        finally:
            import anyio
            with anyio.CancelScope(shield=True):
                await connector.disconnect()

        return state

    @activity.defn
    async def plan_query(self, state: SQLAgentState) -> SQLAgentState:
        return state

    @activity.defn
    async def generate_sql(self, state: SQLAgentState) -> SQLAgentState:
        update = await self.gen_node(state)
        state.update(update)
        return state

    @activity.defn
    async def execute_sql(self, state: SQLAgentState) -> SQLAgentState:
        sql = (state.get("sql_query") or "").strip()
        source_id = state.get("source_id", "default_source")
        
        session_did = AgentNamingService.generate_session_did(state.get("tenant_id", "default"), state.get("session_id", "default"))
        agent_did = AgentNamingService.generate_agent_did("sql_execution_sandbox", session_did)
        
        base_url = os.environ.get("AXIOM_API_URL", "http://localhost:8080")
        target_db_url = f"{base_url}/mcp/postgres/sse"
        
        connector = MCPConnector(source_id, target_db_url, {"headers": {"X-Agent-DID": agent_did}})
        
        try:
            activity.heartbeat("Executing SQL in sandbox...")
            await connector.connect()
            result = await connector.execute_query(sql)
            state["sql_result"] = json.dumps(result, default=str)
            state["error"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            import anyio
            with anyio.CancelScope(shield=True):
                await connector.disconnect()
            
        return state
