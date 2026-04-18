import logging
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from axiom.agent.graph import build_graph
from axiom.agent.thread import ThreadManager
from axiom.security.guard import LakeraGuard

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)

app = FastAPI(title="Axiom Brain", version="0.1.0")

_guard = LakeraGuard()
_agent = None
_thread_mgr = None


@app.on_event("startup")
async def startup() -> None:
    global _agent, _thread_mgr
    _agent = await build_graph()
    _thread_mgr = ThreadManager()


class QueryRequest(BaseModel):
    question: str
    session_id: str = ""
    thread_id: str = ""


class QueryResponse(BaseModel):
    sql: str
    result: str
    session_id: str
    thread_id: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    if not await _guard.is_safe(req.question):
        raise HTTPException(status_code=400, detail="Input blocked by security policy.")

    session_id = req.session_id or str(uuid.uuid4())
    thread_id = req.thread_id or str(uuid.uuid4())

    # Fetch conversation history and check staleness
    history_context, is_stale = await _thread_mgr.get_context_injection(thread_id, "")

    # Check for exact match in cache (prevents collision bug)
    cached = await _thread_mgr.get_cached_result(thread_id, req.question)
    if cached:
        return QueryResponse(
            sql=cached["sql"],
            result=cached["result"],
            session_id=session_id,
            thread_id=thread_id,
        )

    state = await _agent.ainvoke(
        {
            "question": req.question,
            "schema_context": "",
            "sql_query": None,
            "sql_result": None,
            "error": None,
            "attempts": 0,
            "session_id": session_id,
            "thread_id": thread_id,
            "history_context": history_context,
            "is_stale": is_stale,
            "query_type": "NEW_TOPIC",
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    if state.get("error"):
        raise HTTPException(status_code=422, detail=state["error"])

    sql = state["sql_query"] or ""
    result = state["sql_result"] or ""

    # Cache the result for exact match replay
    if sql and result:
        await _thread_mgr.set_cached_result(thread_id, req.question, sql, result)

    return QueryResponse(
        sql=sql,
        result=result,
        session_id=session_id,
        thread_id=thread_id,
    )
