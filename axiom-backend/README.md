# AXIOM Backend

> High-performance backend for the AXIOM agent orchestration platform.
> RAG · Agents · FastAPI · PostgreSQL · Redis · Evals · Observability · AI Safety

---

## Architecture

```
src/axiom/
├── main.py               # FastAPI app factory + lifespan
├── config.py             # Pydantic-settings (type-safe env config)
│
├── api/
│   ├── middleware.py     # Request ID, timing
│   └── v1/
│       ├── health.py     # /health/live  /health/ready
│       ├── rag.py        # /rag/ingest   /rag/query
│       ├── agents.py     # /agents/run   /agents/runs/{id}  /agents/tools
│       └── evals.py      # /evals/datasets  /evals/runs
│
├── core/
│   ├── logging.py        # structlog — JSON (prod) / pretty (dev)
│   ├── tracing.py        # OpenTelemetry OTLP export
│   ├── metrics.py        # Prometheus counters + histograms
│   ├── exceptions.py     # Domain exceptions + FastAPI handlers
│   └── security.py       # Bearer auth + Redis rate limiter
│
├── db/
│   ├── session.py        # Async SQLAlchemy 2.0 session factory
│   ├── base.py           # Declarative base + TimestampMixin
│   └── models/           # Document/Chunk, Conversation, AgentRun, EvalDataset/Run/Result
│
├── cache/
│   └── redis_client.py   # Async Redis pool + cache helpers
│
├── llm/
│   ├── client.py         # Provider-agnostic interface (BYOM)
│   └── providers/
│       ├── anthropic_client.py
│       └── openai_client.py
│
├── rag/
│   ├── ingest.py         # Chunk (tiktoken) -> embed -> persist
│   ├── retriever.py      # pgvector cosine search
│   ├── reranker.py       # LLM-as-judge reranking
│   └── pipeline.py       # retrieve -> rerank -> generate + citations
│
├── agents/
│   ├── executor.py       # Bounded tool-use loop (safety + tracing)
│   └── tools/
│       ├── __init__.py   # @tool decorator + ToolRegistry
│       └── builtin.py    # knowledge_search, calculator, http_get
│
├── safety/
│   ├── guardrails.py     # guard_input / guard_output
│   ├── prompt_injection.py
│   └── pii.py
│
├── evals/
│   ├── metrics.py        # exact_match, contains, llm_judge, rag_faithfulness
│   └── runner.py         # Concurrent eval loop -> EvalRun summary
│
└── workers/
    └── tasks.py          # ARQ background tasks
```

---

## Quick Start

### 1 — Prerequisites

- Docker + Docker Compose
- Python 3.12+

### 2 — Configure

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY (and/or OPENAI_API_KEY) and SECRET_KEY
```

### 3 — Spin up + migrate

```bash
make docker-up
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed
```

Open **http://localhost:8000/docs**

---

## Development (local)

```bash
docker compose up -d postgres redis
make install
make migrate
make seed
make dev          # uvicorn --reload :8000

# In another terminal
make worker       # arq background worker
```

---

## Running Tests

```bash
docker compose up -d postgres redis
make test
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET  | /v1/health/live                    | Liveness probe |
| GET  | /v1/health/ready                   | Readiness (DB + Redis) |
| POST | /v1/rag/ingest                     | Chunk, embed, store docs |
| POST | /v1/rag/query                      | RAG answer with citations |
| POST | /v1/agents/run                     | Execute agent run |
| GET  | /v1/agents/runs/{id}               | Fetch run + trace |
| GET  | /v1/agents/tools                   | List tools |
| POST | /v1/evals/datasets                 | Create eval dataset |
| POST | /v1/evals/datasets/{id}/examples   | Add examples |
| POST | /v1/evals/runs                     | Start eval run (async) |
| GET  | /v1/evals/runs/{id}                | Poll status + summary |

All endpoints require `Authorization: Bearer <token>`.

---

## Adding a Tool

```python
from axiom.agents.tools import tool, ToolContext

@tool(
    name="my_tool",
    description="Does something useful.",
    parameters={
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    },
)
async def my_tool(ctx: ToolContext, args: dict) -> dict:
    return {"result": args["input"].upper()}
```

---

## Adding an LLM Provider (BYOM)

1. Create `src/axiom/llm/providers/myprovider_client.py` implementing `LLMClient`.
2. Register it in `get_llm_client()` in `src/axiom/llm/client.py`.
3. Add the API key to `.env.example` and `config.py`.

---

## Observability

| Signal  | Endpoint / Sink |
|---------|----------------|
| Metrics | GET /metrics (Prometheus) |
| Traces  | OTLP gRPC -> Jaeger / Tempo / Honeycomb |
| Logs    | JSON stdout -> Loki / Datadog / CloudWatch |

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| pgvector over Qdrant/Pinecone | Single DB = joins, ACID, Alembic migrations |
| ARQ over Celery | Async-native, Redis-backed, zero extra broker |
| structlog | JSON in prod, colorized in dev, zero config change |
| guard_input before every LLM call | Token limit + injection detection + PII in one pass |
| Eval runner as background task | Large evals take minutes — async + poll fits the UX |
