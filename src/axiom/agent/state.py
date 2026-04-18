from typing import Optional
from typing_extensions import TypedDict


class SQLAgentState(TypedDict):
    question: str
    schema_context: str
    sql_query: Optional[str]
    sql_result: Optional[str]
    error: Optional[str]
    attempts: int
    session_id: str
    thread_id: str
    history_context: str
    is_stale: bool
    query_type: str
