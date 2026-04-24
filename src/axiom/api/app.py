import logging
import uuid
import json
from datetime import date, datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from axiom.agent.graph import build_graph
from axiom.agent.thread import ThreadManager
from axiom.security.guard import LakeraGuard
from axiom.api.onboard import run_ingestion
from axiom.security.auth import verify_token
from axiom.config import settings

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)

app = FastAPI(title="Axiom Brain", version="0.1.0")

# --- Security: Robust CORS ---
# In development, we allow localhost and any local network IP on port 3000 or 3001.
# For production, this should be restricted to the actual domain.
import re

origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]

# Add a more flexible check for local IP addresses in development
# Note: CORSMiddleware.allow_origin_regex could be used but allow_origins is more explicit.
# We will handle it by allowing the hardcoded IP if it matches a pattern
# OR just adding a few common ones. 
# Better yet, let's keep it simple and add the 10.x.x.x, 192.x.x.x common patterns if needed,
# but for now let's just make it easier to add new ones.
allowed_origin_regex = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9]+\.[0-9]+):(3000|3001)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=allowed_origin_regex.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_guard = LakeraGuard()
_agent = None
_thread_mgr = ThreadManager()
from axiom.rag.schema import SchemaRAG
_rag = SchemaRAG()
_artifact_store = None
_temporal_client = None


@app.on_event("startup")
async def startup() -> None:
    global _agent, _thread_mgr, _rag, _artifact_store, _temporal_client
    # LangGraph Deprecation: _agent is now legacy
    # _agent = await build_graph()
    from axiom.notebooks.artifacts import NotebookArtifactStore
    _artifact_store = NotebookArtifactStore(settings.artifact_root)

    # Initialize Temporal Client
    from temporalio.client import Client
    try:
        _temporal_client = await Client.connect("localhost:7233")
        logger.info("Connected to Temporal.io")
    except Exception as e:
        logger.warning(f"Failed to connect to Temporal: {e}. Event-sourced orchestration disabled.")

    # --- Initialize MCP Hub ---

    from axiom.connectors.mcp.hub import hub
    from axiom.connectors.postgres_server import PostgresMCPServer
    from axiom.rag.mcp_server import KnowledgeMCPServer
    
    postgres_mcp = PostgresMCPServer(settings.database_url)
    knowledge_mcp = KnowledgeMCPServer(_rag)
    
    hub.register_server("postgres", postgres_mcp.get_server())
    hub.register_server("knowledge", knowledge_mcp.get_server())
    app.include_router(hub.router)
    
    # --- Initialize A2A Router ---
    from axiom.agent.temporal.a2a import a2a
    app.include_router(a2a.router)


# --- Models ---

class QueryRequest(BaseModel):
    question: str
    session_id: str = ""
    thread_id: str = ""
    tenant_id: str = "default_tenant"
    source_id: Optional[str] = None
    model: Optional[str] = None


class ApproveRequest(BaseModel):
    thread_id: str
    session_id: str = ""
    tenant_id: str = "default_tenant"
    approved: bool = True
    model: Optional[str] = None


class QueryResponse(BaseModel):
    sql: str
    result: Any # Use Any to allow dict/str, then validate to string
    artifact: Optional[Dict[str, Any]] = None
    insight: Optional[str] = None
    thought: Optional[str] = None
    layout: str = "default"
    action_bar: List[str] = []
    probing_options: List[Dict[str, Any]] = []
    session_id: str
    thread_id: str
    tenant_id: str
    status: str = "completed"

    @field_validator("result", mode="before")
    @classmethod
    def ensure_json_string(cls, v: Any) -> str:
        if isinstance(v, (dict, list)):
            return json.dumps(v, default=str)
        return str(v) if v is not None else ""


class FeedbackRequest(BaseModel):
    thread_id: str
    message_id: str
    is_correct: bool
    comment: Optional[str] = None


class SourceIn(BaseModel):
    tenant_id: str
    source_id: str
    db_url: str
    db_type: str = "postgresql"
    description: str = ""
    mcp_config: Any = None # Use Any to allow raw string from frontend
    custom_rules: Any = None

    @field_validator("mcp_config", mode="before")
    @classmethod
    def parse_json_config_in(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    @field_validator("custom_rules", mode="before")
    @classmethod
    def parse_custom_rules_in(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            try:
                # Store as JSON string if it's a valid JSON object/array
                json.loads(v)
                return v
            except Exception:
                return v
        return v


class SourceOut(BaseModel):
    source_id: str
    tenant_id: str
    name: str
    description: Optional[str]
    db_type: str
    status: str = "active"
    error_message: Optional[str] = None
    mcp_config: Any = None # CRITICAL: Must be Any to receive raw DB string
    custom_rules: Any = None

    @field_validator("mcp_config", mode="before")
    @classmethod
    def parse_json_config_out(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    @field_validator("custom_rules", mode="before")
    @classmethod
    def parse_custom_rules_out(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                return v
        return v


class TenantIn(BaseModel):
    name: str
    id: str # Slug


class TenantOut(BaseModel):
    id: str
    name: str
    owner_id: str
    created_at: datetime


# --- Internal Helpers ---

import asyncpg

# --- API Endpoints ---

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/feedback")
async def save_feedback(req: FeedbackRequest, user_id: str = Depends(verify_token)) -> dict:
    try:
        # Load the turn from history to identify the "Wrong Path"
        if _thread_mgr is None: raise HTTPException(status_code=500)
        history = await _thread_mgr.get_history(req.thread_id)
        
        # In a real app, we'd find the specific message by ID. 
        # For now, we'll use the last turn since it's the one usually being flagged.
        if not history: raise HTTPException(status_code=404, detail="History not found")
        
        last_turn = history[-1]
        
        if not req.is_correct:
            # Generate the Negative Constraint Graft
            # Identify the tables used in the wrong SQL
            from sqlglot import exp, parse_one
            wrong_tables = []
            try:
                parsed = parse_one(last_turn["sql"])
                for table in parsed.find_all(exp.Table):
                    wrong_tables.append(table.name)
            except:
                pass
            
            constraint = f"FAIL_PATH: Query '{last_turn['question']}' using tables {wrong_tables} was flagged WRONG. Reason: {req.comment or 'Incorrect result'}. DO NOT USE THESE TABLES FOR THIS INTENT AGAIN."
            
            # Persist this to the thread metadata so the next turn's MemoryManager picks it up
            metadata = await _thread_mgr.get_thread_metadata(req.thread_id)
            constraints = metadata.get("negative_constraints", [])
            constraints.append(constraint)
            metadata["negative_constraints"] = constraints
            
            # Update Redis
            client = await _thread_mgr._get_client()
            key = f"axiom:thread:{req.thread_id}"
            data = await client.get(key)
            if data:
                parsed_data = json.loads(data)
                parsed_data["metadata"] = metadata
                await client.setex(key, 86400, json.dumps(parsed_data))
                
        return {"status": "feedback_recorded"}
    except Exception as exc:
        logger.exception("Failed to record feedback")
        raise HTTPException(status_code=500, detail=str(exc))


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
            owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
            logger.info("DEBUG: owner=%s, user_id=%s, tenant=%s", owner, user_id, tenant_id)
            if owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: Access to this workspace is restricted.")

            rows = await conn.fetch(
                "SELECT source_id, tenant_id, name, description, db_type, status, error_message, mcp_config, custom_rules FROM data_sources WHERE tenant_id = $1", 
                tenant_id
            )
            # Explicitly parse the rows to ensure mcp_config is dictionary-ready
            results = []
            for r in rows:
                d = dict(r)
                # If mcp_config is a string, parse it manually here too as a backup
                if isinstance(d.get("mcp_config"), str):
                    try:
                        d["mcp_config"] = json.loads(d["mcp_config"])
                    except:
                        pass
                
                # Parse custom_rules string if needed
                if isinstance(d.get("custom_rules"), str):
                    try:
                        d["custom_rules"] = json.loads(d["custom_rules"])
                    except:
                        pass
                results.append(SourceOut(**d))
            return results
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list sources: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch data sources")


@app.post("/api/sources")
async def create_source(req: SourceIn, background_tasks: BackgroundTasks, user_id: str = Depends(verify_token)) -> dict:
    try:
        background_tasks.add_task(
            run_ingestion,
            tenant_id=req.tenant_id,
            source_id=req.source_id,
            db_url=req.db_url,
            db_type=req.db_type,
            description=req.description,
            mcp_config=req.mcp_config,
            custom_rules=req.custom_rules
        )
        return {"status": "ingestion_started", "source_id": req.source_id}
    except Exception as exc:
        logger.exception("Failed to start ingestion: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sources/{tenant_id}/{source_id}/sync")
async def sync_source(tenant_id: str, source_id: str, background_tasks: BackgroundTasks, user_id: str = Depends(verify_token)) -> dict:
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
            if owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: Access to this workspace is restricted.")

            row = await conn.fetchrow(
                "SELECT db_url, db_type, description, mcp_config, custom_rules FROM data_sources WHERE tenant_id = $1 AND source_id = $2",
                tenant_id, source_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Source not found")
                
            mcp_config = row["mcp_config"]
            if isinstance(mcp_config, str):
                try:
                    mcp_config = json.loads(mcp_config)
                except:
                    pass
            
            custom_rules = row["custom_rules"]
            if isinstance(custom_rules, str):
                try:
                    custom_rules = json.loads(custom_rules)
                except:
                    pass

            background_tasks.add_task(
                run_ingestion,
                tenant_id=tenant_id,
                source_id=source_id,
                db_url=row["db_url"],
                db_type=row["db_type"],
                description=row["description"] or "",
                mcp_config=mcp_config,
                custom_rules=custom_rules
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
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
            logger.info("DEBUG: owner=%s, user_id=%s, tenant=%s", owner, user_id, tenant_id)
            if owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to update this source.")

            fields = []
            values = []
            for i, (k, v) in enumerate(req.items(), start=1):
                if k in ["name", "description", "db_url", "db_type", "custom_rules", "mcp_config"]:
                    fields.append(f"{k} = ${i}")
                    if k in ["mcp_config", "custom_rules"] and v is not None and not isinstance(v, str):
                        values.append(json.dumps(v))
                    else:
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update source: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/sources/{tenant_id}/{source_id}")
async def delete_source(tenant_id: str, source_id: str, user_id: str = Depends(verify_token)) -> dict:
    try:
        conn = await asyncpg.connect(settings.database_url)
        try:
            owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
            logger.info("DEBUG: owner=%s, user_id=%s, tenant=%s", owner, user_id, tenant_id)
            if owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to delete this source.")

            await conn.execute(
                "DELETE FROM data_sources WHERE tenant_id = $1 AND source_id = $2", 
                tenant_id, source_id
            )
            return {"status": "deleted"}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete source: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/threads")
async def list_threads(tenant_id: str = "default_tenant", user_id: str = Depends(verify_token)) -> List[Dict[str, Any]]:
    try:
        if _thread_mgr is None:
             raise HTTPException(status_code=500, detail="Thread manager not initialized")
        threads = await _thread_mgr.list_threads(tenant_id)
        return threads
    except Exception as exc:
        logger.exception("Failed to list threads: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/threads/{thread_id}")
async def get_thread_history(thread_id: str, user_id: str = Depends(verify_token)) -> Dict[str, Any]:
    try:
        if _thread_mgr is None:
             raise HTTPException(status_code=500, detail="Thread manager not initialized")
        history = await _thread_mgr.get_history(thread_id)
        metadata = await _thread_mgr.get_thread_metadata(thread_id)
        return {"turns": history, "metadata": metadata}
    except Exception as exc:
        logger.exception("Failed to fetch thread history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, user_id: str = Depends(verify_token)) -> Dict[str, Any]:
    try:
        if _artifact_store is None:
            raise HTTPException(status_code=500, detail="Artifact store not initialized")
        metadata = _artifact_store.load_metadata(artifact_id)
        notebook = _artifact_store.load_notebook(artifact_id)
        from axiom.notebooks.artifacts import NotebookArtifactStore

        return {
            "artifact": NotebookArtifactStore.public_metadata(metadata),
            "notebook": notebook,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch artifact: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, user_id: str = Depends(verify_token)) -> FileResponse:
    try:
        if _artifact_store is None:
            raise HTTPException(status_code=500, detail="Artifact store not initialized")
        path = _artifact_store.notebook_path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(artifact_id)
        return FileResponse(
            path,
            media_type="application/x-ipynb+json",
            filename=f"axiom-{artifact_id}.ipynb",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to download artifact: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/artifacts/{artifact_id}/rerun")
async def rerun_artifact(artifact_id: str, user_id: str = Depends(verify_token)) -> Dict[str, Any]:
    try:
        if _artifact_store is None:
            raise HTTPException(status_code=500, detail="Artifact store not initialized")

        metadata = _artifact_store.load_metadata(artifact_id)
        notebook = _artifact_store.load_notebook(artifact_id)

        from axiom.notebooks.executor_client import NotebookExecutorClient

        executor = NotebookExecutorClient(
            settings.notebook_executor_url,
            settings.notebook_execution_timeout,
        )
        try:
            execution = await executor.execute(
                tenant_id=metadata["tenant_id"],
                thread_id=metadata["thread_id"],
                artifact_id=artifact_id,
                notebook=notebook,
            )
        except Exception as exc:
            execution = {
                "status": "failed",
                "notebook": notebook,
                "outputs": [],
                "execution_error": str(exc),
                "logs": str(exc),
            }

        artifact = _artifact_store.save(
            artifact_id=artifact_id,
            tenant_id=metadata["tenant_id"],
            thread_id=metadata["thread_id"],
            notebook=execution.get("notebook") or notebook,
            status=execution.get("status", "failed"),
            outputs=execution.get("outputs", []),
            cells_summary=metadata.get("cells_summary", []),
            execution_error=execution.get("execution_error"),
            logs=execution.get("logs"),
        )
        return {
            "artifact": artifact,
            "notebook": _artifact_store.load_notebook(artifact_id),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to rerun artifact: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/query/stream")
async def query_stream(req: QueryRequest, user_id: str = Depends(verify_token)):
    if not await _guard.is_safe(req.question):
        raise HTTPException(status_code=400, detail="Input blocked by security policy.")

    session_id = req.session_id or str(uuid.uuid4())
    thread_id = req.thread_id or str(uuid.uuid4())
    tenant_id = req.tenant_id
    config = {"configurable": {"thread_id": thread_id}}

    history_context, is_stale = await _thread_mgr.get_context_injection(thread_id, "")

    initial_state = {
        "question": req.question,
        "selected_tables": [],
        "schema_context": "",
        "few_shot_examples": "",
        "custom_rules": "",
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
        "query_type": "",  # memory_manager sets this to REFINEMENT or NEW_TOPIC
        "artifact": None,
        "llm_model": req.model,
    }

    async def event_generator():
        def _json_serial(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if hasattr(obj, '__dict__'):
                return obj.__dict__
            return str(obj)

        try:
            if not _temporal_client:
                yield f"data: {json.dumps({'error': 'Temporal unavailable'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            handle = await _temporal_client.start_workflow(
                "SQLAgentWorkflow", # Use string name to avoid circular import if needed
                initial_state,
                id=f"sql-agent-stream-{thread_id}",
                task_queue="sql-agent-tasks",
            )

            last_emitted_node = None
            
            while True:
                desc = await handle.describe()
                if desc.status == 1: # Running
                    try:
                        state = await handle.query("get_state")
                        # Determine current progress based on state changes
                        # This is a simplified version of node-based streaming
                        current_node = None
                        if state.get("sql_query") and not state.get("sql_result"):
                             current_node = "generate_sql"
                        elif state.get("schema_context") and not state.get("sql_query"):
                             current_node = "retrieve_schema"
                        
                        if current_node and current_node != last_emitted_node:
                            yield f"data: {json.dumps({current_node: state}, default=_json_serial)}\n\n"
                            last_emitted_node = current_node
                            
                        # Check if waiting for approval
                        if state.get("sql_query") and not state.get("sql_result") and not state.get("error"):
                             # If we've been in this state for a bit, it's likely paused for HITL
                             pass 
                    except Exception:
                        pass
                else:
                    # Workflow finished (Completed, Failed, etc.)
                    final_state = await handle.result()
                    yield f"data: {json.dumps({'__final__': final_state, '__is_paused__': False}, default=_json_serial)}\n\n"
                    break
                
                await asyncio.sleep(0.5)

            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception("Error in stream")
            yield f"data: {json.dumps({'error': str(e)}, default=_json_serial)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, user_id: str = Depends(verify_token)) -> QueryResponse:
    try:
        if not await _guard.is_safe(req.question):
            raise HTTPException(status_code=400, detail="Input blocked by security policy.")

        session_id = req.session_id or str(uuid.uuid4())
        thread_id = req.thread_id or str(uuid.uuid4())
        tenant_id = req.tenant_id
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

        history_context, is_stale = await _thread_mgr.get_context_injection(thread_id, "")

        cached = await _thread_mgr.get_cached_result(thread_id, req.question)
        if cached:
            return QueryResponse(
                sql=cached["sql"],
                result=cached["result"],
                session_id=session_id,
                thread_id=thread_id,
                tenant_id=tenant_id
            )

        initial_state = {
            "question": req.question,
            "selected_tables": [],
            "schema_context": "",
            "few_shot_examples": "",
            "custom_rules": "",
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
            "query_type": "",
            "artifact": None,
            "llm_model": req.model,
        }

        # --- Phase 3: Trigger Temporal Workflow ---
        if _temporal_client:
            from axiom.agent.temporal.workflows import SQLAgentWorkflow
            
            logger.info(f"Starting Temporal Workflow for thread {thread_id}")
            handle = await _temporal_client.start_workflow(
                SQLAgentWorkflow.run,
                initial_state,
                id=f"sql-agent-{thread_id}",
                task_queue="sql-agent-tasks",
            )
            
            # Poll for results with a timeout to see if it paused for approval
            # In a production SSE setup, this would be handled via events.
            # For this REST endpoint, we poll for up to 30s.
            import asyncio
            for _ in range(30):
                desc = await handle.describe()
                # If the workflow is still running, it might be waiting for approval
                # or still executing activities.
                # We check the execution history or use a Query to get current state.
                # Simplification: we'll assume if it's running after activities, it's paused.
                # Better: implement a Query handler in the workflow to return state.
                
                # For this implementation, we wait a bit and then check state via handle.query
                await asyncio.sleep(1)
                try:
                    # We'll add this query handler to the workflow next
                    state = await handle.query("get_state")
                    if state.get("sql_query") and not state.get("sql_result") and not state.get("error"):
                        is_paused = True
                        break
                    if state.get("sql_result") or state.get("error"):
                        is_paused = False
                        break
                except Exception:
                    continue
            
            if is_paused:
                status = "pending_approval"
            else:
                state = await handle.result()
                status = "completed"
        else:
            # Fallback (e.g. for tests if temporal not running)
            raise HTTPException(status_code=503, detail="Temporal service unavailable")

        # Extract values for response
        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""
        error = state.get("error")
        
        layout = state.get("layout", "default")
        action_bar = state.get("action_bar", [])
        probing_options = state.get("probing_options", [])

        if sql and not await _guard.is_safe(sql):
            logger.warning("Security Violation: Generated SQL blocked by Lakera Guard: %s", sql)
            return QueryResponse(
                sql=sql,
                result="",
                insight="Security Violation: The generated query was blocked.",
                layout=layout,
                action_bar=action_bar,
                session_id=session_id,
                thread_id=thread_id,
                tenant_id=tenant_id,
                status="completed"
            )

        if sql and result and status == "completed":
            await _thread_mgr.set_cached_result(thread_id, req.question, sql, result)

        return QueryResponse(
            sql=sql,
            result=result or error or "", # Pass error if no result
            artifact=state.get("artifact"),
            insight=state.get("response_text") if not error else f"I encountered a database error: {error}",
            thought=state.get("agent_thought"),
            layout=layout,
            action_bar=action_bar,
            probing_options=probing_options,
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
        # --- Phase 3: Signal Temporal Workflow ---
        if _temporal_client:
            handle = _temporal_client.get_workflow_handle(f"sql-agent-{req.thread_id}")
            
            # Send signal
            await handle.signal("approve", req.approved)
            
            if not req.approved:
                 return QueryResponse(
                    sql="", result="", session_id=req.session_id, 
                    thread_id=req.thread_id, tenant_id=req.tenant_id, status="rejected"
                )

            # Wait for workflow completion after approval
            state = await handle.result()
            is_paused = False
            status = "completed"
        else:
             raise HTTPException(status_code=503, detail="Temporal service unavailable")

        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""
        
        layout = state.get("layout", "default")
        action_bar = state.get("action_bar", [])
        probing_options = state.get("probing_options", [])

        if state.get("error") and not state.get("sql_result") and not is_paused:
            raise HTTPException(status_code=422, detail=state["error"])

        if sql and result and status == "completed":
            question = state.get("question", "")
            source_id = state.get("source_id", "default_source")
            if question:
                await _thread_mgr.set_cached_result(req.thread_id, question, sql, result)
                # Ensure we index this thread for the tenant if it was a successful execution
                await _thread_mgr.save_turn(
                    req.thread_id,
                    req.tenant_id,
                    question,
                    sql,
                    result,
                    active_filters=state.get("active_filters", []),
                    verified_joins=state.get("verified_joins", []),
                    error_log=state.get("error_log", []),
                    llm_model=req.model,
                    source_id=source_id,
                )
                if _rag:
                    await _rag.search_semantic_cache(req.tenant_id, source_id, question) # Trigger ingest on success

        return QueryResponse(
            sql=sql,
            result=result,
            artifact=state.get("artifact"),
            insight=state.get("response_text"),
            thought=state.get("agent_thought"),
            layout=layout,
            action_bar=action_bar,
            probing_options=probing_options,
            session_id=req.session_id,
            thread_id=req.thread_id,
            tenant_id=req.tenant_id,
            status=status
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Internal error during approval processing: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
