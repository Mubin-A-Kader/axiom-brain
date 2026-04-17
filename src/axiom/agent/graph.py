from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis import RedisSaver

from axiom.agent.nodes import SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode
from axiom.agent.state import SQLAgentState
from axiom.config import settings
from axiom.rag.schema import SchemaRAG


def _should_correct(state: SQLAgentState) -> str:
    if state.get("error") and state["attempts"] < settings.max_correction_attempts:
        return "generate_sql"
    return END


def build_graph(connector_script: str = "src/axiom/connectors/postgres_server.py") -> StateGraph:
    rag = SchemaRAG()

    schema_node = SchemaRetrievalNode(rag)
    gen_node = SQLGenerationNode()
    exec_node = SQLExecutionNode(connector_script)

    graph = StateGraph(SQLAgentState)
    graph.add_node("retrieve_schema", schema_node)
    graph.add_node("generate_sql", gen_node)
    graph.add_node("execute_sql", exec_node)

    graph.set_entry_point("retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_conditional_edges("execute_sql", _should_correct)

    checkpointer = RedisSaver(settings.redis_url)
    return graph.compile(checkpointer=checkpointer)
