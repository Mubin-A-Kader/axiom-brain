import logging

from langgraph.graph import StateGraph, END

from axiom.agent.nodes import (
    SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode, 
    TableSelectionNode, DatabaseSelectionNode, HumanApprovalNode, 
    DataStorytellingNode, ResponseSynthesizerNode, SQLCriticNode, DiscoveryNode
)
from axiom.agent.memory_manager import MemoryManagerNode
from axiom.agent.planner import QueryPlannerNode
from axiom.agent.probing import IntentProberNode
from axiom.agent.state import SQLAgentState
from axiom.agent.thread import ThreadManager
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


def _should_correct(state: SQLAgentState) -> str:
    error = state.get("error")
    sql_result = state.get("sql_result")
    attempts = state.get("attempts", 0)
    
    if attempts >= settings.max_correction_attempts:
        if not error and sql_result:
            return "visualize_data"
        return END

    if error:
        if "does not exist" in error.lower():
            return "discovery"
        return "critic"
    
    if sql_result:
        import json
        try:
            res = json.loads(sql_result)
            if res.get("total_count") == 0:
                return "discovery"
        except Exception:
            pass
        return "visualize_data"
        
    return END

def _should_probe(state: SQLAgentState) -> str:
    # If the user has NOT yet confirmed a source, and we found alternative candidates, we MUST probe.
    has_confirmed = len(state.get("confirmed_tables", [])) > 0
    
    if not has_confirmed and state.get("probing_options") and len(state["probing_options"]) >= 2:
        logger.info("Mandatory Probing Triggered: User must confirm business intent.")
        return "require_probing"
        
    return "plan_query"

def _should_synthesize(state: SQLAgentState) -> str:
    return "synthesize_response"


async def build_graph():
    rag = SchemaRAG()
    thread_mgr = ThreadManager()

    memory_node = MemoryManagerNode()
    db_routing_node = DatabaseSelectionNode()
    routing_node = TableSelectionNode(rag)
    schema_node = SchemaRetrievalNode(rag)
    prober_node = IntentProberNode()
    planner_node = QueryPlannerNode()
    gen_node = SQLGenerationNode(rag)
    critic_node = SQLCriticNode()
    discovery_node = DiscoveryNode()
    exec_node = SQLExecutionNode(thread_mgr)
    approval_node = HumanApprovalNode()
    viz_node = DataStorytellingNode()
    synthesizer_node = ResponseSynthesizerNode()

    graph = StateGraph(SQLAgentState)
    graph.add_node("memory_manager", memory_node)
    graph.add_node("route_database", db_routing_node)
    graph.add_node("route_tables", routing_node)
    graph.add_node("retrieve_schema", schema_node)
    graph.add_node("intent_prober", prober_node)
    graph.add_node("require_probing", approval_node) # Reuse pass-through
    graph.add_node("plan_query", planner_node)
    graph.add_node("generate_sql", gen_node)
    graph.add_node("critic", critic_node)
    graph.add_node("discovery", discovery_node)
    graph.add_node("execute_sql", exec_node)
    graph.add_node("visualize_data", viz_node)
    graph.add_node("synthesize_response", synthesizer_node)

    graph.set_entry_point("memory_manager")
    graph.add_edge("memory_manager", "route_database")
    graph.add_edge("route_database", "route_tables")
    graph.add_edge("route_tables", "retrieve_schema")

    # Proactive Probing Loop
    graph.add_edge("retrieve_schema", "intent_prober")
    graph.add_conditional_edges("intent_prober", _should_probe)
    graph.add_edge("require_probing", "plan_query")

    graph.add_edge("plan_query", "generate_sql")

    # Execution & Correction Loop
    graph.add_edge("generate_sql", "execute_sql")

    graph.add_conditional_edges("execute_sql", _should_correct)
    graph.add_edge("critic", "generate_sql")

    graph.add_edge("discovery", "generate_sql")
    graph.add_edge("visualize_data", "synthesize_response")
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

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["require_probing"],
    )
