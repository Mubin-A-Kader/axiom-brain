# Axiom Brain: Onboarding Guide

This guide explains how to onboard a new tenant and their database sources into the Axiom Brain ecosystem.

## 1. Prerequisites
- **Axiom Infrastructure**: Ensure Postgres, Redis, and ChromaDB are running (usually via `docker compose up -d`).
- **Control Plane**: The central database that stores tenant and source metadata.

## 2. Initialize the Control Plane
If you are setting up the project for the first time or need to ensure the schema is correct:

```bash
uv run python scripts/init_control_plane.py
```
This script creates/migrates the `data_sources` table in your primary Axiom database.

## 3. Onboard a New Database Source
Axiom supports a **Hybrid Architecture**: high-performance direct connectors and universal MCP adapters.

### A. PostgreSQL (Direct)
```bash
uv run python -m axiom.api.cli ingest \
  --tenant acme_corp \
  --source sales_db \
  --type postgresql \
  --url "postgresql://user:pass@host:5432/db" \
  --desc "Sales transactions"
```

### B. MySQL (Direct)
```bash
uv run python -m axiom.api.cli ingest \
  --tenant acme_corp \
  --source marketing_db \
  --type mysql \
  --url "mysql://user:pass@host:3306/db" \
  --desc "Marketing campaign data"
```

### C. Universal MCP (Snowflake, SQL Server, etc.)
You can onboard any database that has an MCP server by providing the command.
```bash
uv run python -m axiom.api.cli ingest \
  --tenant acme_corp \
  --source snowflake_data \
  --type mcp \
  --mcp-command "npx -y snowflake-mcp" \
  --desc "Enterprise Snowflake warehouse"
```
*Note: Ensure required environment variables (e.g., `SNOWFLAKE_ACCOUNT`) are exported in your terminal.*

## 4. Querying via CLI
You can test your onboarding immediately using the built-in query tool:
```bash
uv run python -m axiom.api.cli query "Show me total sales by month" \
  --tenant acme_corp \
  --source sales_db
```

## 5. How it Works
1. **Schema Extraction**: The appropriate Connector (Direct or MCP) inspects the database.
2. **RAG Ingestion**:
   - Table summaries are ingested for routing.
   - Precise DDLs are ingested for SQL generation.
3. **Control Plane Registration**: Connection details are saved to the Axiom Control Plane with the correct `db_type` and pooling configurations.
4. **LRU Caching**: During query execution, Axiom maintains an LRU cache of connection pools for peak enterprise performance.
