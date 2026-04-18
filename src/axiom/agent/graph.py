import logging

from langgraph.graph import StateGraph, END

from axiom.agent.nodes import SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode
from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger(__name__)


def _should_correct(state: SQLAgentState) -> str:
    if state.get("error") and state["attempts"] < settings.max_correction_attempts:
        return "generate_sql"
    return END


async def build_graph():
    rag = SchemaRAG()

    schema_node = SchemaRetrievalNode(rag)
    gen_node = SQLGenerationNode()
    exec_node = SQLExecutionNode()

    graph = StateGraph(SQLAgentState)
    graph.add_node("retrieve_schema", schema_node)
    graph.add_node("generate_sql", gen_node)
    graph.add_node("execute_sql", exec_node)

    graph.set_entry_point("retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_conditional_edges("execute_sql", _should_correct)

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        checkpointer = AsyncRedisSaver.from_conn_string(settings.redis_url)
        await checkpointer.asetup()
        logger.info("Using Redis checkpointer at %s", settings.redis_url)
    except Exception as exc:
        from langgraph.checkpoint.memory import MemorySaver
        logger.warning("Redis unavailable (%s), falling back to MemorySaver", exc)
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)
