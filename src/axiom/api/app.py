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
    tenant_id: str = "default_tenant"


class QueryResponse(BaseModel):
    sql: str
    result: str
    session_id: str
    thread_id: str
    tenant_id: str

def get_tenant_rules(tenant_id: str) -> str:
    """Mock database lookup for tenant-specific SQL rules."""
    rules = {
        "clinical_corp": "Always treat 'deleted' patients as excluded. Join patient_id to user_id.",
        "hr_startup": "Only include 'active' candidates. Salary must not be selected unless requested."
    }
    return rules.get(tenant_id, "")

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    try:
        if not await _guard.is_safe(req.question):
            raise HTTPException(status_code=400, detail="Input blocked by security policy.")

        session_id = req.session_id or str(uuid.uuid4())
        thread_id = req.thread_id or str(uuid.uuid4())
        tenant_id = req.tenant_id

        # Fetch conversation history and check staleness
        history_context, is_stale = await _thread_mgr.get_context_injection(thread_id, "")

        # Check for exact match in cache
        cached = await _thread_mgr.get_cached_result(thread_id, req.question)
        if cached:
            logger.info("Cache hit for thread %s", thread_id)
            return QueryResponse(
                sql=cached["sql"],
                result=cached["result"],
                session_id=session_id,
                thread_id=thread_id,
                tenant_id=tenant_id
            )

        logger.info("Invoking agent for question: %s [Tenant: %s]", req.question, tenant_id)
        state = await _agent.ainvoke(
            {
                "question": req.question,
                "selected_tables": [],
                "schema_context": "",
                "few_shot_examples": "",
                "custom_rules": get_tenant_rules(tenant_id),
                "tenant_id": tenant_id,
                "sql_query": None,
                "sql_result": None,
                "error": None,
                "attempts": 0,
                "session_id": session_id,
                "thread_id": thread_id,
                "history_context": history_context,
                "is_stale": is_stale,
                "query_type": "", # Planner will determine this
            },
            config={"configurable": {"thread_id": thread_id}},
        )

        logger.info("Agent execution finished. Final error: %s", state.get("error"))
        
        # If there is an error and no result, force a 422 error response
        if state.get("error") and not state.get("sql_result"):
            logger.error("Agent failed after %d attempts: %s", state.get("attempts", 0), state["error"])
            raise HTTPException(status_code=422, detail=state["error"])

        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""

        # Cache the result for exact match replay
        if sql and result:
            await _thread_mgr.set_cached_result(thread_id, req.question, sql, result)

        return QueryResponse(
            sql=sql,
            result=result,
            session_id=session_id,
            thread_id=thread_id,
            tenant_id=tenant_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Internal error during query processing: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
