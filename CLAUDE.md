# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is
Axiom Brain is a "Reasoning-as-Infrastructure" Text-to-SQL agent. A natural-language question flows through a LangGraph state machine that retrieves schema context, generates SQL, self-corrects on execution errors, and can escalate into a Root-Cause-Analysis (RCA) investigation loop that produces a Jupyter notebook artifact.

Stack: LangGraph + LiteLLM (via OpenAI SDK) + ChromaDB + NetworkX + Redis (checkpointer + cache) + FastAPI backend + Next.js frontend + Supabase GoTrue for auth.

## Commands

**Python (backend, uv + Python ≥3.12):**
```bash
uv sync                                           # install (incl. dev extras: uv sync --extra dev)
uv run uvicorn axiom.api.app:app --reload --port 8080
uv run python -m axiom.api.cli ingest --tenant <t> --source <s> --type postgresql --url <url>
uv run python -m axiom.api.cli query "<question>" --tenant <t> --source <s>
uv run python scripts/init_control_plane.py      # creates control-plane tables (tenants, data_sources)
uv run pytest                                     # all tests (pytest-asyncio auto mode)
uv run pytest tests/test_agent.py::test_name -v   # single test
uv run ruff check src/                            # lint
uv run mypy src/                                  # typecheck (strict)
```

**Frontend (Next.js 16 + React 19, in `frontend/`):**
```bash
npm run dev     # next dev
npm run build
npm run lint
```
Note: `frontend/AGENTS.md` warns this is Next.js 16 with breaking API changes from training-data-era Next.js — consult `node_modules/next/dist/docs/` before writing frontend code.

**Docker (full stack):**
```bash
docker compose up -d   # postgres(pgvector), redis, chromadb, litellm, auth(gotrue), gateway(nginx), axiom, notebook-executor
```
For dev, bring up infra only and run the app on the host:
```bash
docker compose up -d postgres redis chromadb litellm auth gateway notebook-executor
```

## Architecture (Big Picture)

### Agent graph (`src/axiom/agent/graph.py`)
The compiled LangGraph is the heart of the system. Flow:

```
memory_manager → route_database → route_tables → retrieve_schema
  → problem_definition → hypothesis_generation → investigation_loop
       ↓ (router)
    generate_sql → execute_sql
       ↓ (_should_correct)
    ├─ sql_result ok      → investigation_loop
    ├─ "does not exist"   → discovery      → generate_sql
    ├─ other error        → critic         → generate_sql
    └─ attempts exhausted → investigation_loop
  → action_plan → build_notebook_artifact → synthesize_response → END
```

Checkpointer: `AsyncRedisSaver` with a `MemorySaver` fallback if Redis is unreachable. State lives in `SQLAgentState` (TypedDict, `agent/state.py`) — note it carries both SQL-loop fields (`sql_query`, `attempts`, `error_log`, `verified_joins`) and RCA fields (`problem_statement`, `hypotheses`, `investigation_log`, `rca_report`).

Nodes are class-based with `__call__`; deps injected via constructor (see `nodes.py`, `rca_nodes.py`, `planner.py`, `probing.py`, `memory_manager.py`). RCA nodes in `rca_nodes.py` drive the hypothesis-driven investigation; SQL nodes in `nodes.py` drive generation/execution/criticism.

### Schema RAG (`src/axiom/rag/schema.py`)
ChromaDB holds three document types per source: *DDL schemas* (for generation), *table summaries* (for routing), and *sample rows* (for context). Embeddings are generated via `openai.AsyncOpenAI` against the LiteLLM proxy (`text-embedding-3-large`, 3072 dims) — no LlamaIndex, no litellm Python client. Retrieval is hybrid: vector similarity via ChromaDB + BM25 keyword search via `rank_bm25`, merged with BM25 hits ranked first. `SchemaRAG` also maintains an in-memory `networkx.Graph` for FK-aware neighbor expansion. All searches are filtered by `{"tenant_id": ..., "source_id": ...}`. `ingest()` is async — always `await rag.ingest(...)`.

### Connectors (`src/axiom/connectors/`)
`ConnectorFactory` is a classmethod-based LRU cache (max 20) of `BaseConnector` instances keyed by `source_id`. Built-ins registered lazily: `postgresql` (asyncpg), `mysql` (aiomysql), and an experimental `mcp` adapter. Each connector exposes `dialect_name` and `llm_prompt_instructions` used by `generate_sql`.

### Control plane vs. target databases
- **Control plane**: the Axiom-owned Postgres at `DATABASE_URL`, holding `tenants`, `data_sources`, and Supabase `auth.*` schema. Bootstrapped via `scripts/init_control_plane.py`.
- **Target databases**: the tenants' databases the agent queries. Registered in `data_sources`, accessed via `ConnectorFactory`. Security: execution node enforces `startswith("SELECT")`; generated SQL is also run through Lakera Guard.

### Notebook artifacts (`src/axiom/notebooks/`)
After RCA concludes, `build_notebook_artifact` assembles a Jupyter notebook (code cells + query outputs + charts) and posts it to the `notebook-executor` sidecar (runs on port 8090, separate FastAPI app at `notebooks/executor_app.py`). The executor runs the notebook in a sandboxed container (`mem_limit: 1g`), saves outputs to a shared volume (`/tmp/axiom-artifacts`), and the main API exposes `/artifacts/{id}` endpoints for fetch/download/rerun.

### Auth (`src/axiom/security/auth.py` + `gateway` service)
Supabase GoTrue runs behind an nginx "gateway" that rewrites paths so the Supabase JS SDK works against a self-hosted GoTrue. FastAPI endpoints use `Depends(verify_token)` which validates the JWT against `SUPABASE_JWKS_URL`. Every write endpoint additionally checks `tenants.owner_id = user_id` before touching `data_sources`.

### API endpoints (`src/axiom/api/app.py`)
- `POST /query` (blocking) and `POST /query/stream` (SSE, stream mode `updates`): run the graph. Stream strips heavy base64/SVG payloads before yielding chunks.
- `POST /approve`: resume a paused thread (HITL pattern — the graph used to interrupt before `execute_sql`; confirm current behavior in `graph.py` before assuming).
- `POST /api/feedback`: records negative constraints keyed to the thread so `MemoryManagerNode` can inject "don't use tables X for this intent" on subsequent turns.
- `/api/tenant`, `/api/sources/*`: control-plane CRUD. `create_source` and `sync_source` trigger `run_ingestion` as a `BackgroundTask`.
- `/artifacts/{id}`, `/artifacts/{id}/download`, `/artifacts/{id}/rerun`: notebook lifecycle.

## Key Design Decisions
- **LiteLLM via OpenAI SDK, not the litellm client.** Always use `openai.AsyncOpenAI(base_url=f"{settings.litellm_url}/v1", api_key=settings.litellm_key)`. The litellm client's provider auto-detection has been known to hijack Gemini requests and bypass the proxy. This rule applies to embeddings too — `SchemaRAG._embed()` uses `AsyncOpenAI`, not LiteLLMEmbedding or any LlamaIndex wrapper.
- **Embedding model is `text-embedding-3-large` (3072 dims).** Registered in `litellm_config.yaml` as `openai/text-embedding-3-large`. Changing the model requires wiping and re-ingesting the ChromaDB collection (`chroma_data` volume) — dimensions are fixed at collection creation time.
- **Schema-first prompt ordering.** `generate_sql` places schema context before the question to maximize vLLM prefix-cache hits.
- **Lakera Guard is a no-op** when `LAKERA_API_KEY` is empty — safe to develop without a key. Applied to both user input and generated SQL.
- **Redis checkpointer with MemorySaver fallback.** Don't assume Redis is always available in code paths that read checkpointer state.
- **asyncpg direct, not MCP, for Postgres.** The MCP adapter exists but direct connectors are the production path.
- **Background ingestion.** `create_source` returns immediately; ingestion status should be read from `data_sources.status` / `error_message`, not awaited on the request.
- **Control-plane writes use `asyncpg.connect` + `finally: close`**, not a pool. Keep this pattern when adding endpoints — mixing connection styles has burned us before.

## Repository Layout
- `src/axiom/agent/` — graph, nodes, state, RCA, planner, probing, memory manager, thread manager
- `src/axiom/api/` — FastAPI app, CLI (`axiom.api.cli`), onboarding, query runner
- `src/axiom/connectors/` — `base.py`, `factory.py`, `direct/` (postgres, mysql), `mcp_adapter.py`, `dialects.py`
- `src/axiom/rag/schema.py` — ChromaDB + NetworkX GraphRAG
- `src/axiom/notebooks/` — artifact builder, executor sidecar app, executor client, artifact store
- `src/axiom/security/` — Lakera guard, JWT verification
- `src/axiom/core/` — `cleansing`, `discovery`, `inference` (adaptive inference manager)
- `frontend/src/` — Next.js 16 app (auth, data sources, chat, notebook artifact viewer)
- `scripts/init_control_plane.py` — bootstraps control-plane tables; run once on fresh Postgres
- `tests/` — pytest + deepeval; `asyncio_mode = "auto"` in `pyproject.toml`
- `DOCS/MEMORY_MAP.md`, `DOCS/ONBOARDING.md`, `DOCUMENTATION.md`, `VISION.md`, `GEMINI.md` — narrative docs
