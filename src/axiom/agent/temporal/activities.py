import logging
import asyncio
import json
from temporalio import activity

from axiom.agent.state import SQLAgentState
from axiom.agent.nodes import (
    SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode,
    TableSelectionNode, DatabaseSelectionNode,
    NotebookArtifactNode, ResponseSynthesizerNode, SQLCriticNode, DiscoveryNode,
    PythonCodeGenerationNode,
)
from axiom.agent.memory_manager import MemoryManagerNode
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager

logger = logging.getLogger("axiom-activities")


async def _heartbeat_loop(message: str, interval: float = 5.0) -> None:
    """Send Temporal heartbeats during long-running node calls."""
    while True:
        activity.heartbeat(message)
        await asyncio.sleep(interval)


class SQLActivities:
    """
    Wraps each LangGraph node as a Temporal activity.
    """

    def __init__(self, rag: SchemaRAG, thread_mgr: ThreadManager) -> None:
        self.memory_node = MemoryManagerNode()
        self.db_node = DatabaseSelectionNode()
        self.table_node = TableSelectionNode(rag)
        self.schema_node = SchemaRetrievalNode(rag)
        self.gen_node = SQLGenerationNode(rag)
        self.exec_node = SQLExecutionNode(thread_mgr, rag)
        self.critic_node = SQLCriticNode()
        self.discovery_node = DiscoveryNode()
        self.notebook_node = NotebookArtifactNode()
        self.python_gen_node = PythonCodeGenerationNode()
        self.synthesizer_node = ResponseSynthesizerNode()

    async def _run_node(self, node, state: SQLAgentState, heartbeat_msg: str) -> SQLAgentState:
        hb = asyncio.create_task(_heartbeat_loop(heartbeat_msg))
        try:
            update = await node(state)
            state.update(update)
        finally:
            hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)
        return state

    @activity.defn
    async def memory_manager(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.memory_node, state, "Memory Manager...")

    @activity.defn
    async def route_database(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.db_node, state, "Routing database...")

    @activity.defn
    async def route_tables(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.table_node, state, "Routing tables...")

    @activity.defn
    async def retrieve_schema(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.schema_node, state, "Retrieving schema...")

    @activity.defn
    async def generate_sql(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.gen_node, state, "Generating SQL...")

    @activity.defn
    async def execute_sql(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.exec_node, state, "Executing SQL...")

    @activity.defn
    async def run_critic(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.critic_node, state, "Running critic...")

    @activity.defn
    async def run_discovery(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.discovery_node, state, "Running discovery...")

    @activity.defn
    async def generate_python_code(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.python_gen_node, state, "Generating dynamic analysis code...")

    @activity.defn
    async def build_notebook(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.notebook_node, state, "Building notebook...")

    @activity.defn
    async def synthesize_response(self, state: SQLAgentState) -> SQLAgentState:
        return await self._run_node(self.synthesizer_node, state, "Synthesizing response...")
