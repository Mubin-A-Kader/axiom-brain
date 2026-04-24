# Axiom Brain: Production-Grade Multi-Agent Infrastructure

## Overview

Axiom Brain has evolved from a monolithic Text-to-SQL agent into a "Reasoning-as-Infrastructure" platform. It utilizes a decentralized multi-agent system (MAS) built on top of **Temporal.io** for orchestration and the **Model Context Protocol (MCP)** for secure, standardized resource access.

---

## Core Architecture (The Modern Stack)

The new architecture follows a **Protocol-First, Zero Trust** design:

1.  **Durable Orchestration (Temporal.io):** Replaced LangGraph with Temporal for event-sourced, fault-tolerant execution. Workflows can survive system crashes and provide "Time-Travel Debugging."
2.  **MCP Hub (Universal Connectivity):** A centralized hub hosting multiple MCP servers over HTTP/SSE. This decouples agents from direct resource access (DBs, Vector Stores, etc.).
3.  **Zero Trust Security:**
    *   **ANS & DIDs:** Every agent and user session is issued a Decentralized Identifier (DID).
    *   **ABAC PEP:** An Attribute-Based Access Control Policy Enforcement Point intercepts every tool call to verify permissions and prevent cross-tenant leakage.
    *   **Dual-LLM Monitoring:** The "Virtual Donkey" pattern scans tool payloads for adversarial intent (e.g., Toxic Agent Flows) using an independent security model.
    *   **Cryptographic Tagging:** External data is wrapped in `<untrusted_data>` semantic delimiters to protect downstream agents from Prompt Infection.
4.  **PASTE (Speculative Execution):** Pattern-Aware Speculative Tool Execution launches predicted tool calls (like schema lookups) in parallel with LLM generation to mask latency.
5.  **Hardware Sandboxing:** SQL execution is wrapped in a simulation of Firecracker MicroVMs, providing hardware-level isolation for untrusted code.

---

## Technical Components

### 1. The Orchestrator (`src/axiom/agent/temporal/`)
*   **Workflows (`workflows.py`):** Defines the `SQLAgentWorkflow`, which manages the high-level logic, HITL signals, and self-correction loops.
*   **Activities (`activities.py`):** Deterministic tasks (Retrieve Schema, Generate SQL, Execute SQL) executed by Temporal Workers.
*   **A2A (`a2a.py`):** Implements the Agent2Agent protocol for inter-agent delegation using JSON-RPC.

### 2. The MCP Hub (`src/axiom/connectors/mcp/`)
*   **Hub (`hub.py`):** A FastAPI-based router that manages SSE connections and message routing to registered MCP servers.
*   **Registry (`registry.py`):** Provides connection pooling for MCP SSE sessions to eliminate handshake overhead.
*   **Postgres Server (`src/axiom/connectors/postgres_server.py`):** A production-grade MCP server using `asyncpg` for data retrieval.

### 3. Trust & Security (`src/axiom/security/trust/`)
*   **ANS (`ans.py`):** Agent Naming Service for DIDs.
*   **PEP (`pep.py`):** Policy Enforcement Point and ABAC engine.
*   **Monitor (`monitor.py`):** Dual-LLM payload safety scanner.
*   **Tagging (`tagging.py`):** Cryptographic semantic delimiter implementation.

---

## Workflows

### Standard Query Path
1.  **Ingress:** API receives a natural language question.
2.  **Speculation:** PASTE predicts needed tables and triggers speculative schema retrieval in the background.
3.  **Workflow Start:** Temporal starts the `SQLAgentWorkflow`.
4.  **Retrieval:** The `retrieve_schema` activity calls the Knowledge MCP Server (secured via ABAC).
5.  **Generation:** LLM generates SQL based on tagged schema context.
6.  **HITL Interrupt:** Workflow pauses and sends a signal to the API/Frontend for human approval.
7.  **Execution:** Upon approval, the `execute_sql` activity triggers a Sandboxed MCP call to the Postgres Server.
8.  **Output:** Result is tagged, saved to history, and returned.

---

## Deployment & Development

### Local Setup
```bash
# 1. Start Infrastructure
docker compose up -d

# 2. Start Temporal Worker
uv run python src/axiom/agent/temporal/worker.py

# 3. Start API Server
uv run uvicorn axiom.api.app:app --port 8080
```

### Testing the Stack
Use the validation script to verify Zero Trust and Hub connectivity:
```bash
uv run python scripts/test_mcp_hub.py
```

---

## Roadmap

1.  **True MicroVM Integration:** Replace the Firecracker simulation with actual `fcvm` binary calls.
2.  **Distributed Session Store:** Move `MCPHub` transports to Redis to support multi-pod scaling.
3.  **Advanced Pattern Analyzer:** Train a small model (e.g., T5 or a distilled Llama) to replace heuristics in PASTE.
