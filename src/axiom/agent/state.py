from typing import Optional
from typing_extensions import TypedDict


class SQLAgentState(TypedDict):
    question: str
    selected_tables: list[str]
    schema_context: str
    few_shot_examples: str
    custom_rules: str
    tenant_id: str
    source_id: Optional[str]
    db_type: Optional[str]
    sql_query: Optional[str]
    sql_result: Optional[str]
    error: Optional[str]
    attempts: int
    session_id: str
    thread_id: str
    history_context: str
    is_stale: bool
    query_type: str
    artifact: Optional[dict]
    layout: str
    action_bar: list[str]
    llm_model: Optional[str]
    response_text: Optional[str]
    agent_thought: Optional[str]
    critic_feedback: Optional[str]
    logical_blueprint: Optional[str]
    active_filters: list[str]
    verified_joins: list[str]
    error_log: list[str]
    negative_constraints: list[str]
    probing_options: list[dict]
    confirmed_tables: list[str]
    history_tables: list[str]
    last_sql_result: Optional[str]  # last non-CONCLUDED sql_result, used by notebook builder after action_plan clears sql_result
    # RCA specific fields
    problem_statement: Optional[str]
    hypotheses: list[str]
    validation_results: list[dict]
    investigation_log: list[str]
    investigation_attempts: int
    root_cause_confidence: Optional[str]
    rca_report: Optional[dict]
