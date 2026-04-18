# Axiom Brain — Build Timeline

## What This Is
Text-to-SQL agent ("Reasoning-as-Infrastructure") using the Axiom Stack:
LangGraph + LiteLLM + MCP + ChromaDB + Lakera Guard + Redis

---

## Phase 1 — Scaffold (DONE ✅ 2026-04-17)

**Package management:** `uv` + `pyproject.toml` (Python ≥ 3.11)

**Docker:**
- `Dockerfile` — multi-stage (builder → runtime)
- `docker-compose.yml` — postgres (pgvector), redis, chromadb, litellm, axiom app

**Source tree (`src/axiom/`):**
| Module | File | Status |
|---|---|---|
| Config | `config.py` (pydantic-settings) | ✅ |
| Agent state | `agent/state.py` (TypedDict) | ✅ |
| Agent nodes | `agent/nodes.py` (class-based: Schema/Gen/Exec) | ✅ |
| LangGraph | `agent/graph.py` (self-correction loop + Redis checkpointer) | ✅ |
| Schema RAG | `rag/schema.py` (ChromaDB + NetworkX GraphRAG) | ✅ |
| Security | `security/guard.py` (Lakera Guard, no-op when key absent) | ✅ |
| MCP connector | `connectors/postgres_server.py` (SELECT-only safety) | ✅ |
| API | `api/app.py` (FastAPI `/health` + `/query`) | ✅ |

---

## Phase 2 — Spec-Based Coding (TODO — next session)

- [ ] Schema ingestion CLI (`axiom ingest --url <db>`)
- [ ] `/query` request validation + rate limiting
- [ ] DeepEval test suite (`tests/test_agent.py`)
- [ ] Arize Phoenix tracing wired into LangGraph
- [ ] GitHub Actions CI (lint + typecheck + deepeval)
- [ ] SQLite MCP connector (for local dev without Postgres)
- [ ] Keygen.sh license middleware (Phase 2 product layer)
- [ ] `uv.lock` committed after first `uv sync`

---

## How to Run Locally

```bash
cp .env.example .env
# fill in GEMINI_API_KEY (or OPENAI_API_KEY)

# start infra
docker compose up -d postgres redis chromadb litellm

# run app (outside docker for dev)
uv sync
uv run uvicorn axiom.api.app:app --reload --port 8080
```

Or full stack: `docker compose up -d`

---

## Key Design Decisions
- **Class-based nodes** — each LangGraph node is a class with `__call__`, injectable deps
- **asyncpg for DB access** — direct async connection; MCP connector kept for future swap
- **Lakera Guard is a no-op** when `LAKERA_API_KEY` is empty (safe for local dev)
- **Redis checkpointer** — multi-turn sessions survive restarts; falls back to MemorySaver if Redis down
- **Schema prompt ordering** — schema first, question last (maximises vLLM prefix cache hits)
- **LiteLLM proxy via OpenAI SDK** — use `openai.AsyncOpenAI(base_url=litellm_url/v1)` to avoid litellm client provider detection hijacking Gemini requests
