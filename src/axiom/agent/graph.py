import logging

from langgraph.graph import StateGraph, END

from axiom.agent.nodes import (
    SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode,
    TableSelectionNode, DatabaseSelectionNode,
    NotebookArtifactNode, ResponseSynthesizerNode, SQLCriticNode, DiscoveryNode
)
from axiom.agent.memory_manager import MemoryManagerNode
from axiom.agent.state import SQLAgentState
from axiom.agent.thread import ThreadManager
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


def _should_correct(state: SQLAgentState) -> str:
    error = state.get("error")
    sql_result = state.get("sql_result")
    attempts = state.get("attempts", 0)

    # 1. Success → build notebook artifact
    if sql_result:
        return "build_notebook_artifact"

    # 2. Hard limits or unrecoverable errors → synthesize what we have
    if attempts >= settings.max_correction_attempts:
        return "synthesize_response"

    if error and (
        "Exhausted maximum SQL correction" in error
        or "permission denied" in error.lower()
    ):
        return "synthesize_response"

    # 3. Zero results → critic handles investigation + INVESTIGATE loop
    if error and "ZERO_RESULTS" in error:
        return "critic"

    # 4. Table doesn't exist → try discovery once, then critic
    if error and "does not exist" in error.lower() and attempts <= 1:
        return "discovery"

    # 5. Any other error → critic
    if error:
        return "critic"

    # 6. Fallback
    return "synthesize_response"


async def build_graph():
    rag = SchemaRAG()
    thread_mgr = ThreadManager()

    graph = StateGraph(SQLAgentState)

    graph.add_node("memory_manager", MemoryManagerNode())
    graph.add_node("route_database", DatabaseSelectionNode())
    graph.add_node("route_tables", TableSelectionNode(rag))
    graph.add_node("retrieve_schema", SchemaRetrievalNode(rag))
    graph.add_node("generate_sql", SQLGenerationNode(rag))
    graph.add_node("execute_sql", SQLExecutionNode(thread_mgr, rag))
    graph.add_node("critic", SQLCriticNode())
    graph.add_node("discovery", DiscoveryNode())
    graph.add_node("build_notebook_artifact", NotebookArtifactNode())
    graph.add_node("synthesize_response", ResponseSynthesizerNode())

    graph.set_entry_point("memory_manager")
    graph.add_edge("memory_manager", "route_database")
    graph.add_edge("route_database", "route_tables")
    graph.add_edge("route_tables", "retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_conditional_edges("execute_sql", _should_correct)
    graph.add_edge("critic", "generate_sql")
    graph.add_edge("discovery", "generate_sql")
    graph.add_edge("build_notebook_artifact", "synthesize_response")
    graph.add_edge("synthesize_response", END)

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        checkpointer = AsyncRedisSaver(settings.redis_url)
        await checkpointer.asetup()
        logger.info("Using Redis checkpointer at %s", settings.redis_url)
    except Exception as exc:
        from langgraph.checkpoint.memory import MemorySaver
        logger.warning("Redis unavailable (%s), falling back to MemorySaver", exc)
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)
