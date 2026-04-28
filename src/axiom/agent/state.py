from typing import Optional
from typing_extensions import TypedDict


class GlobalAgentState(TypedDict, total=False):
    # Base Global Context
    question: str
    tenant_id: str
    session_id: str
    thread_id: str
    llm_model: Optional[str]
    history_context: str
    is_stale: bool
    
    # Routing — declared here so values propagate into SQL/App subgraphs
    next_agent: Optional[str]
    source_id: Optional[str]
    attempts: int
    selected_tables: list[str]

    # Data lake — scoped routing
    lake_id: Optional[str]             # specific named lake
    lake_scope: list[str]              # source_ids in scope (plain list for compat)
    lake_scope_meta: list[dict]        # [{source_id, query_mode}, …] — used by orchestrator dispatch
    lake_mode: bool                    # True when multi-source fan-out is active
    lake_worker_results: list[dict]    # serialised LakeWorkerResult per source
    needs_source_clarification: bool   # True when router is ambiguous → HITL
    routing_candidates: list[dict]     # candidate sources surfaced to the user when ambiguous

    # Common Outputs
    response_text: Optional[str]
    agent_thought: Optional[str]
    artifact: Optional[dict]
    layout: str
    action_bar: list[str]
    error: Optional[str]
    sql_query: Optional[str]   # surfaced here so CLI HITL can read it post-interrupt


class SQLAgentState(GlobalAgentState):
    # SQL Specific Context
    selected_tables: list[str]
    schema_context: str
    few_shot_examples: str
    custom_rules: str
    source_id: Optional[str]
    db_type: Optional[str]
    
    # SQL Execution
    sql_query: Optional[str]
    sql_result: Optional[str]
    attempts: int
    query_type: str
    critic_feedback: Optional[str]
    logical_blueprint: Optional[str]
    
    # SQL Memory & Routing
    active_filters: list[str]
    verified_joins: list[str]
    error_log: list[str]
    negative_constraints: list[str]
    probing_options: list[dict]
    confirmed_tables: list[str]
    history_tables: list[str]
    last_sql_result: Optional[str]
    
    # Python Artifacts
    python_code: Optional[str]
    python_error: Optional[str]
    notebook_attempts: int
    
    # RCA Specific Fields
    problem_statement: Optional[str]
    hypotheses: list[str]
    validation_results: list[dict]
    investigation_log: list[str]
    investigation_attempts: int
    root_cause_confidence: Optional[str]
    rca_report: Optional[dict]


class AppAgentState(GlobalAgentState):
    # Generic app connector state (Gmail, Slack, GitHub, ...)
    mcp_tool_results: list[dict]
    app_error: Optional[str]
    python_code: Optional[str]
    python_error: Optional[str]
    notebook_attempts: int
