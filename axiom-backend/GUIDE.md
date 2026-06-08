# AXIOM End-to-End Guide

Welcome to the AXIOM guide. This document provides a narrative tour of the platform, explaining how the different components work together to provide a secure, observable, and high-performance agent orchestration environment.

---

## 1. The Big Picture

AXIOM is built to move LLM applications from "cool demo" to "production grade." It follows a modular architecture:

- **Core API**: FastAPI-based endpoints for RAG, Agents, and Evals.
- **Agent Orchestrator**: A bounded loop that handles tool-use, safety, and tracing.
- **RAG Pipeline**: A multi-stage retrieval system (retrieve -> rerank -> generate).
- **Safety Layer**: Guardrails that intercept every input and output.
- **Eval Engine**: A framework to measure and iterate on performance.
- **Infrastructure**: Postgres (data + vectors), Redis (cache + queue), and OpenTelemetry (observability).

---

## 2. Request Lifecycle: A Deep Dive

Let's trace what happens when you call an endpoint like `/v1/agents/run`.

### Step A: Entry & Middleware
1. The request hits `src/axiom/main.py`.
2. **`RequestIDMiddleware`** assigns a unique ID to the request. This ID is propagated through every log line and trace.
3. **`TimingMiddleware`** starts a clock to measure latency.
4. **`Authentication`**: Handled in `src/axiom/api/deps.py`, it verifies your Bearer token.

### Step B: The Agent Loop (`src/axiom/agents/executor.py`)
Once authorized, the `run_agent` function takes over:

1. **Input Guard**: Before the LLM sees the prompt, `src/axiom/safety/guardrails.py` checks for prompt injection and redacts PII.
2. **The Loop**: The agent enters a `while` loop (up to `MAX_ITERATIONS`).
3. **LLM Call**: It sends the user message + system prompt + tool definitions to the LLM (Anthropic/OpenAI).
4. **Tool Execution**: If the LLM requests a tool (e.g., `knowledge_search`), the executor looks it up in the `registry`, runs it, and feeds the result back to the LLM.
5. **Output Guard**: Once the LLM gives a final answer, it's checked for PII before being returned.
6. **Persistence**: Every step (iteration) is recorded in the database and exported as an OpenTelemetry trace.

---

## 3. The RAG Pipeline (`src/axiom/rag/pipeline.py`)

AXIOM's RAG isn't just a simple vector search. It uses a **Retrieve-Rerank-Generate** pattern:

1. **Retrieval**: `retriever.py` uses `pgvector` to find the top K chunks most semantically similar to the query.
2. **Reranking**: `reranker.py` takes those K chunks and uses a smaller/faster LLM call to score them specifically against the question. This fixes "lost in the middle" problems.
3. **Synthesis**: The top reranked chunks are formatted into a prompt with numbered citations (e.g., `[1]`, `[2]`).
4. **Citations**: The final response includes both the text and a structured `citations` list so the frontend can link back to source documents.

---

## 4. Safety & Trust (`src/axiom/safety/`)

Safety is "baked in," not bolted on:

- **Prompt Injection**: Uses heuristics to detect attempts to bypass system instructions.
- **PII Redaction**: Automatically detects and masks emails, credit cards, and PII in both inputs and outputs.
- **Token Budgeting**: Prevents "denial of wallet" attacks by capping input/output tokens.

---

## 5. Evaluation Engine (`src/axiom/evals/`)

You can't improve what you don't measure. The Eval engine allows you to:

1. **Define Datasets**: Pairs of inputs and "golden" expected outputs.
2. **Run Evals**: Execute your RAG or Agent pipeline against the dataset.
3. **Apply Metrics**:
   - `exact_match`: String comparison.
   - `llm_judge`: Use an LLM to grade the answer on a scale.
   - `rag_faithfulness`: Check if the answer is supported by the retrieved context.

---

## 6. Infrastructure & Observability

- **Database**: We use **Postgres** for everything. `pgvector` allows us to store embeddings alongside our relational data, making joins (e.g., "Find documents owned by User X") trivial.

### Core Database Schema
The database models in `src/axiom/db/models/` reflect the platform's focus:
- **RAG**: `Document` and `Chunk` (with pgvector embeddings).
- **Conversations**: `Conversation`, `Message`, and `AgentRun` (storing full traces).
- **Evals**: `EvalDataset`, `EvalExample`, `EvalRun`, and `EvalResult`.

- **Caching**: **Redis** handles rate-limiting and acts as the broker for background workers.
- **Observability**:
  - **Logs**: Structured JSON logs via `structlog`.
  - **Metrics**: Prometheus metrics for request counts, latencies, and agent success rates.
  - **Traces**: OpenTelemetry traces show you exactly where time is being spent in a complex agent run.

---

## 7. How to Extend AXIOM

### Adding a New Tool
1. Go to `src/axiom/agents/tools/builtin.py` (or create a new file).
2. Use the `@tool` decorator.
3. It will be automatically available to the agent if included in `enabled_tools`.

### Adding a New LLM Provider
1. Implement the `LLMClient` interface in `src/axiom/llm/providers/`.
2. Register it in `src/axiom/llm/client.py`.

---

## 8. Development Workflow

1. **Migrations**: If you change a model in `src/axiom/db/models/`, run `make migrate`.
2. **Seeding**: Use `make seed` to populate the DB with test data.
3. **Testing**: Run `make test` to execute the suite in `tests/`.
4. **Background Tasks**: Some things (like large evals) run in the background. Start the worker with `make worker`.

---

*This project is designed to be highly modular. If you need to swap the Reranker or add a custom Safety guardrail, the interfaces in `src/axiom/` are built to be easily overridden.*
