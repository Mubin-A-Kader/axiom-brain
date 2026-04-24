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
        update = await self.exec_node(state)
        state.update(update)
        return state
