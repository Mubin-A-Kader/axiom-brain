import logging
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from axiom.agent.graph import build_graph
from axiom.security.guard import LakeraGuard

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)

app = FastAPI(title="Axiom Brain", version="0.1.0")

_guard = LakeraGuard()
_agent = build_graph()


class QueryRequest(BaseModel):
    question: str
    session_id: str = ""


class QueryResponse(BaseModel):
    sql: str
    result: str
    session_id: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    if not await _guard.is_safe(req.question):
        raise HTTPException(status_code=400, detail="Input blocked by security policy.")

    session_id = req.session_id or str(uuid.uuid4())

    state = await _agent.ainvoke(
        {
            "question": req.question,
            "schema_context": "",
            "sql_query": None,
            "sql_result": None,
            "error": None,
            "attempts": 0,
            "session_id": session_id,
        },
        config={"configurable": {"thread_id": session_id}},
    )

    if state.get("error"):
        raise HTTPException(status_code=422, detail=state["error"])

    return QueryResponse(
        sql=state["sql_query"] or "",
        result=state["sql_result"] or "",
        session_id=session_id,
    )
