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
    visualization: Optional[str]
    llm_model: Optional[str]
    response_text: Optional[str]
    agent_thought: Optional[str]
