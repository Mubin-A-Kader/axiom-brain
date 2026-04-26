# Axiom Brain - Project Documentation

## Project Overview

Axiom Brain is a "Reasoning-as-Infrastructure" Text-to-SQL agent built using the Axiom Stack. It transforms natural language questions into executable SQL queries, performs execution, and handles self-correction through an iterative LangGraph workflow. It is designed to be highly secure, multi-tenant capable, and robust against hallucinated queries.

## Architecture & Core Technologies

The system relies on a modern, asynchronous tech stack tailored for AI agents:

- **Language:** Python 3.12+
- **Agent Orchestration:** [LangGraph](https://langchain-ai.github.io/langgraph/) ≥1.0 for stateful, cyclic agent workflows.
- **LLM Interface:** LiteLLM proxy (via OpenAI SDK — `openai.AsyncOpenAI(base_url=litellm_url)`) for unified model access. Never use the litellm Python client directly.
- **Embeddings:** `text-embedding-3-large` (3072 dims) via LiteLLM proxy. ChromaDB collection dimension is fixed at creation — changing models requires wiping the `chroma_data` volume and re-ingesting.
- **Schema RAG:** Direct ChromaDB + `rank_bm25` — no LlamaIndex. Hybrid retrieval: vector similarity + BM25, merged with BM25 hits ranked first.
- **API Framework:** [FastAPI](https://fastapi.tiangolo.com/) for exposing the query and approval endpoints.
- **Vector Database:** [ChromaDB](https://www.trychroma.com/) for Schema RAG (DDLs, table summaries, sample rows).
- **Control/Target Database:** [PostgreSQL](https://www.postgresql.org/) (managed via `asyncpg`) for control plane data (`data_sources`) and SQL execution.
- **State Persistence:** [Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/) used as a LangGraph checkpointer (`AsyncRedisSaver`) for persistent conversational threads and interrupts.
- **Security:** [Lakera Guard](https://www.lakera.ai/guard) for prompt injection protection.

## Core Workflows (The LangGraph Agent)

The primary logic is modeled as a state machine (Graph) defined in `src/axiom/agent/graph.py`. The state is defined in `SQLAgentState`.

### 1. Database Routing (`route_database`)
If a `source_id` is not provided in the request, the agent queries the control plane for databases associated with the user's `tenant_id`. If multiple exist, an LLM is used to select the best database source based on the user's question and the source descriptions.

### 2. Table Selection (`route_tables`)
To prevent blowing out the context window, this node queries ChromaDB for "table summaries" related to the user's question. An LLM then selects up to 3 tables most likely needed to answer the query.

### 3. Schema RAG (`retrieve_schema`)
This node retrieves the exact DDL statements for the selected tables from ChromaDB. It utilizes `NetworkX` (graphs) to automatically pull in "neighbor" tables connected via Foreign Keys, ensuring the LLM has complete context to perform accurate SQL `JOIN`s. It also retrieves few-shot examples for the specific source.

### 4. Query Planning (`plan_query`)
*(Node exists in `planner.py`, sets `query_type` and other planner state)*. Helps distinguish between "NEW_TOPIC" and "REFINEMENT" queries to dictate how conversation history is utilized.

### 5. SQL Generation (`generate_sql`)
The LLM generates the SQL query. The prompt strictly enforces:
- Adherence to the retrieved Schema Context.
- Utilizing Conversation History (for resolving pronouns like "his" or "it" to exact literal values).
- Adhering to tenant-specific custom rules fetched from the control plane.
- Outputting the final SQL wrapped in `<sql>` tags and its thought process in `<thought>` tags.

### 6. SQL Execution & Self-Correction (`execute_sql`)
The query is executed against the target database. 
- **Security check:** It strictly enforces that queries start with `SELECT`.
- **Self-Correction:** If execution fails (e.g., column not found syntax error), the graph conditionally loops back to `generate_sql` (`_should_correct`), appending the database error to the prompt. It will attempt this up to a configured `max_correction_attempts` (default: 3).

### 7. Human-in-the-Loop (HITL)
The graph is compiled with `interrupt_before=["execute_sql"]`. This means the graph pauses right before executing the query. The FastAPI endpoint (`/query`) returns a `pending_approval` status. A separate `/approve` endpoint is used to resume the graph and execute the query, providing a mechanism for human review of AI-generated SQL.

## Multi-Tenancy & Database Management

Axiom Brain is built from the ground up for multi-tenancy, handling multiple isolated databases simultaneously.

### The `ConnectorFactory` & LRU Cache
Connections are managed by `ConnectorFactory` (`src/axiom/connectors/factory.py`).
- Maintains an LRU (Least Recently Used) cache of active database connection pools (limit: 20).
- Dynamically loads and connects to databases using a standard `BaseConnector` interface.
- Supports `postgresql`, `mysql`, and experimental `mcp` (Model Context Protocol) adapters.

### Data Isolation
- Everything is scoped by `tenant_id` and `source_id`.
- The Schema RAG vector search filters rigorously by `{"source_id": source_id}` ensuring no schema leakage between different databases or tenants.

## API Endpoints

- `GET /health`: Standard health check.
- `POST /query`: Submits a question. Can return a `completed` status (with results) or a `pending_approval` status (if paused before execution).
- `POST /approve`: Resumes a paused thread to execute the approved SQL query.

## CLI Onboarding
The system includes an onboarding script (`src/axiom/api/onboard.py`) that:
1. Connects to a target database.
2. Extracts its schema.
3. Ingests the DDLs and foreign keys into ChromaDB + NetworkX.
4. Registers the database connection securely in the Control Plane (`data_sources` table).

---

## Future Recommendations / Roadmap

To transition this from a robust prototype to a production-ready enterprise tool:

1. **Distributed Connection Pooling:** Migrate away from in-memory python LRU cache for database connectors. Utilize network-layer poolers like **PgBouncer** and secure secret managers for credentials.
2. **Strict Read-Only Enforcement:** Ensure the database users provided to the `ConnectorFactory` are strictly read-only at the database engine level, as a fallback to the current simple string checking (`startswith("SELECT")`).
3. **Semantic Caching:** Implement a true semantic cache. If a question is semantically identical to a previously approved query, return the cached SQL immediately without invoking the LLM, reducing latency and cost.
4. **Data-Level Security (RLS):** Support passing a specific End-User ID through the API to utilize Postgres Row-Level Security (`SET LOCAL myapp.current_user ...`), ensuring users only see their own data within a shared tenant database.
5. **Secure Vector Storage:** Enable authentication on the local ChromaDB instance or migrate to a managed vector store provider for easier production scaling.
