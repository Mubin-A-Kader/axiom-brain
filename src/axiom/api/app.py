import logging
import uuid

from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from axiom.agent.graph import build_graph
from axiom.agent.thread import ThreadManager
from axiom.security.guard import LakeraGuard
from axiom.api.onboard import run_ingestion
from axiom.security.auth import verify_token

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)

app = FastAPI(title="Axiom Brain", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_guard = LakeraGuard()
_agent = None
_thread_mgr = None
_rag = None


@app.on_event("startup")
async def startup() -> None:
    global _agent, _thread_mgr, _rag
    _agent = await build_graph()
    _thread_mgr = ThreadManager()
    from axiom.rag.schema import SchemaRAG
    _rag = SchemaRAG()


class QueryRequest(BaseModel):
    question: str
    session_id: str = ""
    thread_id: str = ""
    tenant_id: str = "default_tenant"
    source_id: Optional[str] = None


class ApproveRequest(BaseModel):
    thread_id: str
    session_id: str = ""
    tenant_id: str = "default_tenant"
    approved: bool = True


class QueryResponse(BaseModel):
    sql: str
    result: str
    session_id: str
    thread_id: str
    tenant_id: str
    status: str = "completed"

class SourceIn(BaseModel):
    tenant_id: str
    source_id: str
    db_url: str
    db_type: str = "postgresql"
    description: str = ""
    mcp_config: Optional[Dict[str, Any]] = None

class SourceOut(BaseModel):
    source_id: str
    tenant_id: str
    name: str
    description: Optional[str]
    db_type: str
    status: str = "active"
    error_message: Optional[str] = None

class TenantIn(BaseModel):
    name: str
    id: str # Slug

class TenantOut(BaseModel):
    id: str
    name: str
    owner_id: str
    created_at: datetime

import asyncpg
from axiom.config import settings

async def get_tenant_rules(tenant_id: str) -> str:
    """Database lookup for tenant-specific SQL rules aggregated across all data sources."""
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            # Aggregate custom rules from all data sources for this tenant
            rows = await conn.fetch(
                "SELECT custom_rules FROM data_sources WHERE tenant_id = $1", 
                tenant_id
            )
            rules = [r["custom_rules"] for r in rows if r["custom_rules"]]
            # Deduplicate and join
            return "\n".join(list(set(rules)))
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Failed to fetch tenant rules: %s", exc)
        return ""

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/tenant", response_model=Optional[TenantOut])
async def get_tenant(user_id: str = Depends(verify_token)) -> Optional[TenantOut]:
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            row = await conn.fetchrow(
                "SELECT id, name, owner_id, created_at FROM tenants WHERE owner_id = $1", 
                user_id
            )
            return TenantOut(**dict(row)) if row else None
        finally:
            await conn.close()
    except Exception as exc:
        logger.exception("Failed to fetch tenant: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/tenant", response_model=TenantOut)
async def create_tenant(req: TenantIn, user_id: str = Depends(verify_token)) -> TenantOut:
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            # Check if user already has a tenant
            existing = await conn.fetchrow("SELECT id FROM tenants WHERE owner_id = $1", user_id)
            if existing:
                raise HTTPException(status_code=400, detail="User already owns a workspace.")
            
            # Check if slug is taken
            slug_taken = await conn.fetchrow("SELECT id FROM tenants WHERE id = $1", req.id)
            if slug_taken:
                raise HTTPException(status_code=400, detail="Workspace ID is already taken.")

            row = await conn.fetchrow(
                "INSERT INTO tenants (id, name, owner_id) VALUES ($1, $2, $3) RETURNING id, name, owner_id, created_at",
                req.id, req.name, user_id
            )
            return TenantOut(**dict(row))
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create tenant: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/api/sources/{tenant_id}", response_model=List[SourceOut])
async def list_sources(tenant_id: str, user_id: str = Depends(verify_token)) -> List[SourceOut]:
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            # Security check: Does this user own this tenant?
            owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
            if owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: Access to this workspace is restricted.")

            rows = await conn.fetch(
                "SELECT source_id, tenant_id, name, description, db_type, status, error_message FROM data_sources WHERE tenant_id = $1", 
                tenant_id
            )
            return [SourceOut(**dict(r)) for r in rows]
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
...
        logger.exception("Failed to list sources: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch data sources")


@app.post("/api/sources")
async def create_source(req: SourceIn, background_tasks: BackgroundTasks, user_id: str = Depends(verify_token)) -> dict:
    try:
        # Trigger ingestion in background
        background_tasks.add_task(
            run_ingestion,
            tenant_id=req.tenant_id,
            source_id=req.source_id,
            db_url=req.db_url,
            db_type=req.db_type,
            description=req.description,
            mcp_config=req.mcp_config
        )
        return {"status": "ingestion_started", "source_id": req.source_id}
    except Exception as exc:
        logger.exception("Failed to start ingestion: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sources/{tenant_id}/{source_id}/sync")
async def sync_source(tenant_id: str, source_id: str, background_tasks: BackgroundTasks, user_id: str = Depends(verify_token)) -> dict:
    # Security check
    if tenant_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            row = await conn.fetchrow(
                "SELECT db_url, db_type, description, mcp_config FROM data_sources WHERE tenant_id = $1 AND source_id = $2",
                tenant_id, source_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Source not found")
                
            # Trigger ingestion in background
            background_tasks.add_task(
                run_ingestion,
                tenant_id=tenant_id,
                source_id=source_id,
                db_url=row["db_url"],
                db_type=row["db_type"],
                description=row["description"] or "",
                mcp_config=json.loads(row["mcp_config"]) if row["mcp_config"] else None
            )
            return {"status": "sync_started"}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to start sync: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.patch("/api/sources/{tenant_id}/{source_id}")
async def update_source(tenant_id: str, source_id: str, req: Dict[str, Any], user_id: str = Depends(verify_token)) -> dict:
    # Security: Ensure user is modifying their own tenant
    if tenant_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You can only modify your own sources.")
        
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            # Dynamically build update query
            fields = []
            values = []
            for i, (k, v) in enumerate(req.items(), start=1):
                if k in ["name", "description", "db_url", "db_type", "custom_rules"]:
                    fields.append(f"{k} = ${i}")
                    values.append(v)
            
            if not fields:
                return {"status": "no_change"}
                
            values.append(tenant_id)
            values.append(source_id)
            query = f"UPDATE data_sources SET {', '.join(fields)} WHERE tenant_id = ${len(values)-1} AND source_id = ${len(values)}"
            
            res = await conn.execute(query, *values)
            return {"status": "updated", "result": res}
        finally:
            await conn.close()
    except Exception as exc:
        logger.exception("Failed to update source: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/sources/{tenant_id}/{source_id}")
async def delete_source(tenant_id: str, source_id: str, user_id: str = Depends(verify_token)) -> dict:
    # Security: Ensure user is deleting their own tenant
    if tenant_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You can only delete your own sources.")

    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            await conn.execute(
                "DELETE FROM data_sources WHERE tenant_id = $1 AND source_id = $2", 
                tenant_id, source_id
            )
            # Optionally: Clean up ChromaDB as well? 
            # For now, just remove from control plane
            return {"status": "deleted"}
        finally:
            await conn.close()
    except Exception as exc:
        logger.exception("Failed to delete source: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, user_id: str = Depends(verify_token)) -> QueryResponse:
    try:
        if not await _guard.is_safe(req.question):
            raise HTTPException(status_code=400, detail="Input blocked by security policy.")

        session_id = req.session_id or str(uuid.uuid4())
        thread_id = req.thread_id or str(uuid.uuid4())
        tenant_id = req.tenant_id
        config = {"configurable": {"thread_id": thread_id}}

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
                "custom_rules": await get_tenant_rules(tenant_id),
                "tenant_id": tenant_id,
                "source_id": req.source_id,
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
            config=config,
        )

        agent_state = await _agent.aget_state(config)
        is_paused = bool(agent_state.next)

        logger.info("Agent execution finished. Paused: %s. Final error: %s", is_paused, state.get("error"))
        
        # If there is an error and no result, force a 422 error response
        if state.get("error") and not state.get("sql_result") and not is_paused:
            logger.error("Agent failed after %d attempts: %s", state.get("attempts", 0), state["error"])
            raise HTTPException(status_code=422, detail=state["error"])

        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""
        status = "pending_approval" if is_paused else "completed"

        # Cache the result for exact match replay
        if sql and result and status == "completed":
            await _thread_mgr.set_cached_result(thread_id, req.question, sql, result)

        return QueryResponse(
            sql=sql,
            result=result,
            session_id=session_id,
            thread_id=thread_id,
            tenant_id=tenant_id,
            status=status
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Internal error during query processing: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/approve", response_model=QueryResponse)
async def approve(req: ApproveRequest, user_id: str = Depends(verify_token)) -> QueryResponse:

    try:
        config = {"configurable": {"thread_id": req.thread_id}}
        agent_state = await _agent.aget_state(config)
        
        if not agent_state.next:
            raise HTTPException(status_code=400, detail="No pending action to approve for this thread.")

        if not req.approved:
            # If rejected, we might just want to return a rejected status without continuing
            return QueryResponse(
                sql=agent_state.values.get("sql_query", ""),
                result="",
                session_id=req.session_id,
                thread_id=req.thread_id,
                tenant_id=req.tenant_id,
                status="rejected"
            )

        logger.info("Resuming agent execution for thread %s", req.thread_id)
        # Pass None to resume from the current state
        state = await _agent.ainvoke(None, config=config)

        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""

        if state.get("error") and not state.get("sql_result"):
            logger.error("Agent failed after %d attempts: %s", state.get("attempts", 0), state["error"])
            raise HTTPException(status_code=422, detail=state["error"])

        # Cache the result for exact match replay
        if sql and result:
            question = state.get("question", "")
            source_id = state.get("source_id", "default_source")
            if question:
                await _thread_mgr.set_cached_result(req.thread_id, question, sql, result)
                if _rag:
                    await _rag.ingest_example(source_id, question, sql)

        return QueryResponse(
            sql=sql,
            result=result,
            session_id=req.session_id,
            thread_id=req.thread_id,
            tenant_id=req.tenant_id,
            status="completed"
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Internal error during approval processing: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
