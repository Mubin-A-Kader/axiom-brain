import logging
import uuid
import json
import asyncio
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
# anyio cancel-scope teardown noise from MCP stdio_client GC — not actionable
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from axiom.api.oauth_routes import router as oauth_router

app = FastAPI(title="Axiom Brain", version="0.1.0")
app.include_router(oauth_router)

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
    # LangGraph: primary fallback orchestrator + used when Temporal is unavailable
    _agent = await build_graph()
    from axiom.notebooks.artifacts import NotebookArtifactStore
    _artifact_store = NotebookArtifactStore(settings.artifact_root)

    # Temporal: preferred durable orchestrator (optional — graceful degradation to LangGraph)
    from temporalio.client import Client
    try:
        _temporal_client = await Client.connect(settings.temporal_url)
        logger.info(f"Connected to Temporal.io at {settings.temporal_url}")
    except Exception as e:
        logger.warning(f"Temporal unavailable ({e}). Falling back to LangGraph orchestration.")

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
    lake_id: Optional[str] = None        # specific named lake
    lake_scope: Optional[List[str]] = None   # explicit source subset; None = use lake_id or all
    model: Optional[str] = None


class LakeOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    created_at: datetime


class LakeIn(BaseModel):
    name: str
    description: Optional[str] = ""


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
    routing_candidates: List[Dict[str, Any]] = []

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


# ── Data Lake endpoints (Multi-lake support) ────────────────────────────────

@app.get("/api/lakes/{tenant_id}", response_model=List[LakeOut])
async def list_lakes(tenant_id: str, user_id: str = Depends(verify_token)) -> List[LakeOut]:
    conn = await asyncpg.connect(settings.database_url)
    try:
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        rows = await conn.fetch(
            "SELECT id, name, description, created_at FROM lakes WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id
        )
        return [LakeOut(**dict(r)) for r in rows]
    finally:
        await conn.close()


@app.post("/api/lakes/{tenant_id}", response_model=LakeOut)
async def create_lake(tenant_id: str, req: LakeIn, user_id: str = Depends(verify_token)) -> LakeOut:
    conn = await asyncpg.connect(settings.database_url)
    try:
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        
        row = await conn.fetchrow(
            "INSERT INTO lakes (tenant_id, name, description) VALUES ($1, $2, $3) RETURNING id, name, description, created_at",
            tenant_id, req.name, req.description
        )
        return LakeOut(**dict(row))
    finally:
        await conn.close()


@app.delete("/api/lakes/{tenant_id}/{lake_id}")
async def delete_lake(tenant_id: str, lake_id: str, user_id: str = Depends(verify_token)) -> dict:
    conn = await asyncpg.connect(settings.database_url)
    try:
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        
        await conn.execute("DELETE FROM lakes WHERE id = $1 AND tenant_id = $2", lake_id, tenant_id)
        return {"status": "deleted"}
    finally:
        await conn.close()


@app.get("/api/lake-sources/{lake_id}")
async def get_lake_sources(lake_id: str, user_id: str = Depends(verify_token)) -> dict:
    conn = await asyncpg.connect(settings.database_url)
    try:
        # Check lake ownership via tenant
        tenant_id = await conn.fetchval("SELECT tenant_id FROM lakes WHERE id = $1", lake_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Lake not found")
            
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        rows = await conn.fetch(
            """SELECT ls.source_id, ls.added_at, ds.name, ds.db_type, ds.description, ds.status
               FROM lake_sources ls
               JOIN data_sources ds ON ds.source_id = ls.source_id
               WHERE ls.lake_id = $1
               ORDER BY ls.added_at""",
            lake_id,
        )
        return {"sources": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.post("/api/lake-sources/{lake_id}/{source_id:path}")
async def add_source_to_lake(lake_id: str, source_id: str, user_id: str = Depends(verify_token)) -> dict:
    conn = await asyncpg.connect(settings.database_url)
    try:
        tenant_id = await conn.fetchval("SELECT tenant_id FROM lakes WHERE id = $1", lake_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Lake not found")
            
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        
        await conn.execute(
            "INSERT INTO lake_sources (lake_id, source_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            lake_id, source_id,
        )
        return {"status": "added", "source_id": source_id}
    finally:
        await conn.close()


@app.delete("/api/lake-sources/{lake_id}/{source_id:path}")
async def remove_source_from_lake(lake_id: str, source_id: str, user_id: str = Depends(verify_token)) -> dict:
    conn = await asyncpg.connect(settings.database_url)
    try:
        tenant_id = await conn.fetchval("SELECT tenant_id FROM lakes WHERE id = $1", lake_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Lake not found")
            
        owner = await conn.fetchval("SELECT owner_id FROM tenants WHERE id = $1", tenant_id)
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        
        await conn.execute(
            "DELETE FROM lake_sources WHERE lake_id = $1 AND source_id = $2",
            lake_id, source_id,
        )
        return {"status": "removed", "source_id": source_id}
    finally:
        await conn.close()


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
        "lake_id": req.lake_id,
        "lake_scope": req.lake_scope or [],
        "lake_scope_meta": [],
        "lake_mode": False,
        "lake_worker_results": [],
        "needs_source_clarification": False,
        "routing_candidates": [],
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

        def _strip_heavy(d) -> dict:
            if not isinstance(d, dict):
                return {}
            return {
                k: v for k, v in d.items()
                if not (isinstance(v, str) and len(v) > 50_000)
            }

        try:
            # Streaming always goes through LangGraph — it has native astream() support.
            # Temporal cannot stream; polling its workflow state causes the frontend to hang
            # when the workflow is queued (no worker) or paused at the HITL gate.
            if not _agent:
                yield f"data: {json.dumps({'error': 'Agent not initialized'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for event in _agent.astream(initial_state, config=config, stream_mode="updates", subgraphs=True):
                # LangGraph with subgraphs=True returns a tuple (namespace, chunk)
                if isinstance(event, tuple) and len(event) == 2:
                    namespace, chunk = event
                else:
                    chunk = event
                
                node_name = next(iter(chunk))
                if node_name == "__interrupt__":
                    continue
                
                update = chunk[node_name]
                if isinstance(update, dict):
                    yield f"data: {json.dumps({node_name: _strip_heavy(update)}, default=_json_serial)}\n\n"

            # After the stream ends, check if the graph is paused at execute_sql (HITL gate)
            # Use subgraphs=True so we can see the sql_subgraph's internal state (source_id,
            # sql_query, etc.) which is stored in a separate checkpoint namespace and is NOT
            # visible in the outer graph's snapshot.values when interrupted.
            snapshot = await _agent.aget_state(config, subgraphs=True)
            final_state = dict(snapshot.values)
            is_paused = bool(snapshot.next)  # non-empty next means interrupted

            # Flatten the interrupted sql_subgraph's state into final_state so the frontend
            # receives source_id, sql_query, db_type, lake_mode, etc. that were set inside
            # the subgraph before the execute_sql interrupt.
            if is_paused and snapshot.tasks:
                for task in snapshot.tasks:
                    task_state = getattr(task, "state", None)
                    if task_state and hasattr(task_state, "values"):
                        for key, val in dict(task_state.values).items():
                            if final_state.get(key) is None and val is not None:
                                final_state[key] = val

            yield f"data: {json.dumps({'__final__': _strip_heavy(final_state), '__is_paused__': is_paused}, default=_json_serial)}\n\n"
            yield "data: [DONE]\n\n"

            # Persist the completed turn so subsequent turns have history context.
            # Skip when paused at HITL — the turn will be saved after /approve completes.
            if not is_paused and isinstance(final_state, dict):
                try:
                    await _thread_mgr.save_turn(
                        thread_id,
                        tenant_id,
                        req.question,
                        final_state.get("sql_query") or "",
                        final_state.get("sql_result") or "",
                        active_filters=final_state.get("active_filters") or [],
                        verified_joins=final_state.get("verified_joins") or [],
                        error_log=final_state.get("error_log") or [],
                        llm_model=req.model,
                        source_id=final_state.get("source_id"),
                        artifact=final_state.get("artifact"),
                        insight=final_state.get("response_text") or "",
                        thought=final_state.get("agent_thought") or "",
                    )
                except Exception as _save_err:
                    logger.warning("Failed to save stream turn to history: %s", _save_err)
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
            "lake_id": req.lake_id,
            "lake_scope": req.lake_scope or [],
            "lake_scope_meta": [],
            "lake_mode": False,
            "lake_worker_results": [],
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

        # The blocking /query endpoint runs LangGraph directly.
        # Temporal durability applies only to the execution phase (post-approval via /approve),
        # not to the generation phase — a blocking HTTP call is abandoned if the caller
        # disconnects regardless of what Temporal does on the server side.
        if not _agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        logger.info("Running LangGraph for thread %s", thread_id)
        state = await _agent.ainvoke(initial_state, config=config)
        snapshot = await _agent.aget_state(config)
        is_paused = bool(snapshot.next)
        status = "pending_approval" if is_paused else "completed"

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
            question = state.get("question", "")
            source_id = state.get("source_id", "default_source")
            if question:
                await _thread_mgr.set_cached_result(thread_id, req.question, sql, result)
                # Update the turn with the artifact if the query bypassed HITL
                await _thread_mgr.save_turn(
                    thread_id,
                    tenant_id,
                    question,
                    sql,
                    result,
                    active_filters=state.get("active_filters", []),
                    verified_joins=state.get("verified_joins", []),
                    error_log=state.get("error_log", []),
                    llm_model=req.model,
                    source_id=source_id,
                    artifact=state.get("artifact"),
                    insight=state.get("response_text", ""),
                    thought=state.get("agent_thought", ""),
                )
                if _rag:
                    await _rag.search_semantic_cache(tenant_id, source_id, question)

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
        config = {"configurable": {"thread_id": req.thread_id}, "recursion_limit": 50}

        # Rejected — no execution needed regardless of path
        if not req.approved:
            if _agent:
                await _agent.aupdate_state(config, {"error": "Query rejected by user."})
            return QueryResponse(
                sql="", result="", session_id=req.session_id,
                thread_id=req.thread_id, tenant_id=req.tenant_id, status="rejected"
            )

        # ── Read the LangGraph checkpoint to get the state at the HITL pause ──
        # Streaming uses LangGraph up to generate_sql, saving state in Redis.
        # We read that checkpoint here and hand it off to whichever executor runs next.
        if not _agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        snapshot = await _agent.aget_state(config, subgraphs=True)
        if not snapshot or not snapshot.values:
            raise HTTPException(status_code=404, detail="No paused query found for this thread")

        paused_state = dict(snapshot.values)
        # Flatten any interrupted subgraph state so the Temporal path gets source_id,
        # sql_query, etc. that were set inside sql_subgraph before the HITL pause.
        if snapshot.tasks:
            for task in snapshot.tasks:
                task_state = getattr(task, "state", None)
                if task_state and hasattr(task_state, "values"):
                    for key, val in dict(task_state.values).items():
                        if paused_state.get(key) is None and val is not None:
                            paused_state[key] = val

        # ── Temporal path: durable execution with retries, logs, time-travel debug ──
        if _temporal_client:
            try:
                from axiom.agent.temporal.workflows import ExecutionWorkflow
                handle = await _temporal_client.start_workflow(
                    ExecutionWorkflow.run,
                    paused_state,
                    id=f"exec-{req.thread_id}",
                    task_queue="sql-agent-tasks",
                )
                state = await handle.result()
                logger.info("Temporal ExecutionWorkflow completed for thread %s", req.thread_id)
            except Exception as temporal_err:
                logger.warning(
                    "Temporal execution failed for thread %s (%s) — falling back to LangGraph",
                    req.thread_id, temporal_err,
                )
                state = await _agent.ainvoke(None, config=config)

        # ── LangGraph fallback: resumes from Redis checkpoint directly ──
        else:
            state = await _agent.ainvoke(None, config=config)

        status = "completed"
        sql = state.get("sql_query") or ""
        result = state.get("sql_result") or ""

        layout = state.get("layout", "default")
        action_bar = state.get("action_bar", [])
        probing_options = state.get("probing_options", [])

        if state.get("error") and not state.get("sql_result"):
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
                    artifact=state.get("artifact"),
                    insight=state.get("response_text", ""),
                    thought=state.get("agent_thought", ""),
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
