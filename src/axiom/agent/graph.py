import logging

from langgraph.graph import StateGraph, END

from axiom.agent.nodes import SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode, TableSelectionNode, DatabaseSelectionNode, HumanApprovalNode
from axiom.agent.planner import QueryPlannerNode
from axiom.agent.state import SQLAgentState
from axiom.agent.thread import ThreadManager
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


def _should_correct(state: SQLAgentState) -> str:
    if state.get("error") and state["attempts"] < settings.max_correction_attempts:
        return "generate_sql"
    return END

def _should_approve(state: SQLAgentState) -> str:
    if state.get("error"):
        return "execute_sql"

    sql = (state.get("sql_query") or "").upper()
    
    # Non-SELECT queries always require approval
    if not sql.strip().startswith("SELECT"):
        return "require_approval"
        
    # Complex queries with 3 or more JOINs require approval
    if sql.count("JOIN") >= 3:
        return "require_approval"
        
    # Queries hitting potentially sensitive tables require approval
    sensitive_tables = ["USERS", "CUSTOMERS", "PASSWORDS", "CREDENTIALS", "PAYMENTS"]
    if any(table in sql for table in sensitive_tables):
        return "require_approval"
        
    # Otherwise, execute immediately
    return "execute_sql"


async def build_graph():
    rag = SchemaRAG()
    thread_mgr = ThreadManager()

    db_routing_node = DatabaseSelectionNode()
    routing_node = TableSelectionNode(rag)
    schema_node = SchemaRetrievalNode(rag)
    planner_node = QueryPlannerNode()
    gen_node = SQLGenerationNode(rag)
    exec_node = SQLExecutionNode(thread_mgr)
    approval_node = HumanApprovalNode()

    graph = StateGraph(SQLAgentState)
    graph.add_node("route_database", db_routing_node)
    graph.add_node("route_tables", routing_node)
    graph.add_node("retrieve_schema", schema_node)
    graph.add_node("plan_query", planner_node)
    graph.add_node("generate_sql", gen_node)
    graph.add_node("require_approval", approval_node)
    graph.add_node("execute_sql", exec_node)

    graph.set_entry_point("route_database")
    graph.add_edge("route_database", "route_tables")
    graph.add_edge("route_tables", "retrieve_schema")
    graph.add_edge("retrieve_schema", "plan_query")
    graph.add_edge("plan_query", "generate_sql")
    
    # Conditional logic for human-in-the-loop
    graph.add_conditional_edges("generate_sql", _should_approve)
    graph.add_edge("require_approval", "execute_sql")
    
    graph.add_conditional_edges("execute_sql", _should_correct)

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
        interrupt_before=["require_approval"],
    )