import logging

from langgraph.graph import StateGraph, END

from axiom.agent.nodes import (
    SchemaRetrievalNode, SQLGenerationNode, SQLExecutionNode,
    TableSelectionNode, DatabaseSelectionNode,
    LakeOrchestratorNode, LakeCuratorNode,
    NotebookArtifactNode, ResponseSynthesizerNode, SQLCriticNode, DiscoveryNode,
    PythonCodeGenerationNode
)
from axiom.agent.probing import IntentProberNode
from axiom.agent.memory_manager import MemoryManagerNode
from axiom.agent.state import SQLAgentState, AppAgentState, GlobalAgentState
from axiom.agent.thread import ThreadManager
from axiom.agent.supervisor import SupervisorNode
from axiom.agent.app_nodes import AppExecutionNode
from axiom.config import settings
from axiom.rag.schema import SchemaRAG
import axiom.connectors.apps  # registers all app connector manifests

logger = logging.getLogger(__name__)


def _route_after_db_selection(state: SQLAgentState) -> str:
    """Route after DB selection.

    If the router set an error (e.g. lake has no queryable sources), short-circuit
    directly to synthesize_response so the user gets a clear message instead of
    the pipeline burning through 5 LLM attempts against a null source.
    """
    if state.get("error"):
        return "synthesize_response"
    return "lake_orchestrator" if state.get("lake_mode") else "route_tables"


def _route_after_lake_curation(state: SQLAgentState) -> str:
    """Mirror of _should_correct for the lake path.

    When the curator produced a sql_result (data to visualise), continue to
    generate_python_code → build_notebook_artifact exactly as the single-source
    path does.  If the curator produced no data (all workers failed), go
    straight to synthesize_response which will relay the curator's message.
    """
    return "generate_python_code" if state.get("sql_result") else "synthesize_response"


def _should_correct(state: SQLAgentState) -> str:
    error = state.get("error")
    sql_result = state.get("sql_result")
    attempts = state.get("attempts", 0)

    if sql_result:
        return "generate_python_code"

    if attempts >= settings.max_correction_attempts:
        return "synthesize_response"

    if error and (
        "Exhausted maximum SQL correction" in error
        or "permission denied" in error.lower()
    ):
        return "synthesize_response"

    if error and "ZERO_RESULTS" in error:
        return "critic"

    if error and "does not exist" in error.lower() and attempts <= 1:
        return "discovery"

    if error:
        return "critic"

    return "synthesize_response"


def _should_retry_notebook(state: SQLAgentState) -> str:
    """Route back to code generation if execution failed and retries remain."""
    if state.get("python_error") and not state.get("artifact"):
        return "generate_python_code"
    return "synthesize_response"


def _route_after_probing(state: SQLAgentState) -> str:
    """Route after checking schema ambiguity.
    
    If the prober identified multiple unconfirmed candidate tables, 
    short-circuit to synthesize_response so the user is prompted to clarify.
    """
    if state.get("probing_options") or state.get("needs_source_clarification"):
        return "synthesize_response"
    return "retrieve_schema"


def _route_supervisor(state: GlobalAgentState) -> str:
    agent = state.get("next_agent", "DATA_AGENT")
    if agent == "AMBIGUOUS_AGENT":
        return END
    if agent == "DATA_AGENT":
        return "sql_subgraph"
    # Dynamic: "GMAIL_AGENT" → "gmail_subgraph", "SLACK_AGENT" → "slack_subgraph", ...
    name = agent.removesuffix("_AGENT").lower()
    return f"{name}_subgraph"


def _should_generate_app_notebook(state: AppAgentState) -> str:
    question = state.get("question", "").lower()
    if "notebook" in question or "chart" in question or "graph" in question or "plot" in question:
        if state.get("mcp_tool_results"):
            return "generate_python_code"
    return END

def _should_retry_app_notebook(state: AppAgentState) -> str:
    if state.get("python_error") and not state.get("artifact"):
        return "generate_python_code"
    return END

async def build_graph(hitl: bool = True):
    rag = SchemaRAG()
    thread_mgr = ThreadManager()

    # ── 1. SQL Sub-Graph ──
    sql_graph = StateGraph(SQLAgentState)
    sql_graph.add_node("memory_manager", MemoryManagerNode())
    sql_graph.add_node("route_database", DatabaseSelectionNode())

    # Lake fan-out path
    sql_graph.add_node("lake_orchestrator", LakeOrchestratorNode(rag))
    sql_graph.add_node("lake_curator", LakeCuratorNode())

    # Single-source path
    sql_graph.add_node("route_tables", TableSelectionNode(rag))
    sql_graph.add_node("intent_prober", IntentProberNode())
    sql_graph.add_node("retrieve_schema", SchemaRetrievalNode(rag))
    sql_graph.add_node("generate_sql", SQLGenerationNode(rag))
    sql_graph.add_node("execute_sql", SQLExecutionNode(thread_mgr, rag))
    sql_graph.add_node("critic", SQLCriticNode())
    sql_graph.add_node("discovery", DiscoveryNode())
    sql_graph.add_node("generate_python_code", PythonCodeGenerationNode())
    sql_graph.add_node("build_notebook_artifact", NotebookArtifactNode())
    sql_graph.add_node("synthesize_response", ResponseSynthesizerNode())

    sql_graph.set_entry_point("memory_manager")
    sql_graph.add_edge("memory_manager", "route_database")

    # After DB selection: fan-out to lake orchestrator or continue to single-source path.
    # "synthesize_response" is included so routing errors short-circuit cleanly.
    sql_graph.add_conditional_edges(
        "route_database",
        _route_after_db_selection,
        {
            "lake_orchestrator": "lake_orchestrator",
            "route_tables": "route_tables",
            "synthesize_response": "synthesize_response",
        },
    )

    # Lake path: orchestrate → curate → (notebook if data) → synthesize
    sql_graph.add_edge("lake_orchestrator", "lake_curator")
    sql_graph.add_conditional_edges(
        "lake_curator",
        _route_after_lake_curation,
        {"generate_python_code": "generate_python_code", "synthesize_response": "synthesize_response"},
    )

    # Single-source path (unchanged)
    sql_graph.add_edge("route_tables", "intent_prober")
    sql_graph.add_conditional_edges(
        "intent_prober",
        _route_after_probing,
        {"retrieve_schema": "retrieve_schema", "synthesize_response": "synthesize_response"}
    )
    sql_graph.add_edge("retrieve_schema", "generate_sql")
    sql_graph.add_edge("generate_sql", "execute_sql")
    sql_graph.add_conditional_edges("execute_sql", _should_correct)
    sql_graph.add_edge("critic", "generate_sql")
    sql_graph.add_edge("discovery", "generate_sql")
    sql_graph.add_edge("generate_python_code", "build_notebook_artifact")
    sql_graph.add_conditional_edges(
        "build_notebook_artifact",
        _should_retry_notebook,
        {"generate_python_code": "generate_python_code", "synthesize_response": "synthesize_response"},
    )
    sql_graph.add_edge("synthesize_response", END)
    
    # Compile SQL sub-graph — HITL interrupt only for the web app, not CLI
    compiled_sql = sql_graph.compile(
        interrupt_before=["execute_sql"] if hitl else []
    )

    # ── 2. App Sub-Graphs (one per registered manifest) ──
    from axiom.connectors.apps.factory import AppConnectorFactory
    app_subgraphs: dict[str, object] = {}
    for manifest in AppConnectorFactory.all_manifests():
        g = StateGraph(AppAgentState)
        g.add_node("execute", AppExecutionNode(manifest.name))
        g.add_node("generate_python_code", PythonCodeGenerationNode())
        g.add_node("build_notebook_artifact", NotebookArtifactNode())
        
        g.set_entry_point("execute")
        g.add_conditional_edges("execute", _should_generate_app_notebook)
        g.add_edge("generate_python_code", "build_notebook_artifact")
        g.add_conditional_edges("build_notebook_artifact", _should_retry_app_notebook)
        
        app_subgraphs[manifest.name] = g.compile()
        logger.info("Built subgraph for app connector: %s", manifest.name)

    # ── 3. Master Supervisor Graph ──
    main_graph = StateGraph(GlobalAgentState)
    main_graph.add_node("supervisor", SupervisorNode())
    main_graph.add_node("sql_subgraph", compiled_sql)
    for name, compiled_app in app_subgraphs.items():
        main_graph.add_node(f"{name}_subgraph", compiled_app)

    main_graph.set_entry_point("supervisor")
    main_graph.add_conditional_edges("supervisor", _route_supervisor)
    main_graph.add_edge("sql_subgraph", END)
    for name in app_subgraphs:
        main_graph.add_edge(f"{name}_subgraph", END)

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        checkpointer = AsyncRedisSaver(settings.redis_url)
        await checkpointer.asetup()
        logger.info("Using Redis checkpointer at %s", settings.redis_url)
    except Exception as exc:
        from langgraph.checkpoint.memory import MemorySaver
        logger.warning("Redis unavailable (%s), falling back to MemorySaver", exc)
        checkpointer = MemorySaver()

    return main_graph.compile(checkpointer=checkpointer)
