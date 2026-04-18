# Axiom Brain - Memory & State Map

This document outlines all places where state, data, or "memory" is stored, accessed, or mutated within the Axiom Brain application. The system relies on a mix of in-memory structures, Redis for caching/history, and ChromaDB for semantic knowledge.

---

## 1. Short-Term / Volatile Memory (Application Process)

### A. Agent State (`src/axiom/agent/state.py`)
During a single request lifecycle, memory is passed between LangGraph nodes using the `SQLAgentState` `TypedDict`. This state is completely ephemeral per execution (unless checkpointed).
*   **Properties Touched:**
    *   `question`: The user's input query.
    *   `schema_context`: Injected DDLs from ChromaDB.
    *   `sql_query`: The LLM-generated SQL string.
    *   `sql_result`: Output string from the Postgres database execution.
    *   `error`: Exception/error messages if execution fails.
    *   `attempts`: Retry counter for self-correction loops.
    *   `session_id` & `thread_id`: Tracking identifiers.
    *   `history_context`: Formatted string of previous turns.
    *   `is_stale`: Boolean indicating if the thread is older than 30 mins.
    *   `query_type`: Classification of the query intent.

### B. Global Application Instances (`src/axiom/api/app.py`)
The FastAPI server holds singletons in its process memory, initialized at startup:
*   `_agent`: The compiled LangGraph object.
*   `_thread_mgr`: Singleton `ThreadManager` instance.
*   `_guard`: Singleton `LakeraGuard` instance.

### C. RAG In-Memory Graph (`src/axiom/rag/schema.py`)
While ChromaDB holds the vectors, the `SchemaRAG` class maintains an active `networkx.Graph` in memory (`self._graph`).
*   **Purpose:** Fast traversal of table relationships. Nodes represent tables (storing column names), and edges represent Foreign Key references.

---

## 2. Conversation History & Caching (Redis)

Managed by `ThreadManager` (`src/axiom/agent/thread.py`) and LangGraph's checkpointer. Note: Requires **Redis Stack** with RediSearch enabled.

### A. Thread History
*   **Location:** Redis cache
*   **Key Format:** `axiom:thread:{thread_id}`
*   **Data Structure:** JSON object containing `turns` (list) and `last_active` timestamp.
*   **Details:** Keeps the last 5 turns (`_history_size = 5`). Each turn stores the `timestamp`, `question`, `sql`, and database `result`.
*   **Lifecycle:** 24-hour TTL (86400 seconds).

### B. Exact-Match Query Cache
*   **Location:** Redis cache
*   **Key Format:** `axiom:cache:{hash(thread_id + question)}` (SHA-256 hash)
*   **Data Structure:** JSON object containing `sql` and `result`.
*   **Purpose:** Prevents re-running identical questions within the same thread. Prevents unnecessary LLM tokens and DB hits.
*   **Lifecycle:** 1-hour TTL (3600 seconds).

### C. LangGraph Checkpoints (`src/axiom/agent/graph.py`)
*   **Location:** Redis via `AsyncRedisSaver` (or `MemorySaver` fallback).
*   **Purpose:** LangGraph intrinsically saves the state of a thread at each node execution step to allow for pausing, resuming, or human-in-the-loop workflows.

---

## 3. Persistent Semantic Knowledge (ChromaDB)

Managed by `SchemaRAG` (`src/axiom/rag/schema.py`) and populated via `scripts/ingest_schema.py`.

*   **Location:** ChromaDB Vector Database (`settings.chroma_url`)
*   **Collection:** `schema` (defined in config)
*   **Data Structure:**
    *   `documents`: Raw DDL strings (e.g., `CREATE TABLE...`).
    *   `ids`: Table names.
    *   `metadatas`: JSON containing `table` name and a comma-separated list of `columns`.
*   **Purpose:** Used for vector similarity search to map a user's natural language question to relevant database schemas.

---

## 4. Source-of-Truth Target Database (PostgreSQL)

*   **Location:** PostgreSQL (`settings.database_url`)
*   **Purpose:** The business database being queried. Axiom executes SELECT statements against this database to retrieve answers. While not "agent memory", it is the stateful system the agent interacts with.
