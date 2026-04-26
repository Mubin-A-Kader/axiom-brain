# Axiom Brain — Connector Architecture

This document describes how Axiom Brain connects to external applications (Gmail, Slack, GitHub, etc.) and how to add new connectors. It is the authoritative reference for scaling the connector layer to 100+ integrations.

---

## Two Connector Types

Axiom has two distinct connector abstractions. Do not conflate them.

| | **DB Connector** | **App Connector** |
|---|---|---|
| Base class | `BaseConnector` | `AppConnectorManifest` |
| Factory | `ConnectorFactory` | `AppConnectorFactory` (to build) |
| Protocol | SQL over asyncpg / aiomysql | MCP tools over STDIO or SSE |
| Auth | Connection string / SSH tunnel | OAuth2 PKCE, API key, service account |
| Per-tenant | One pool per `source_id` | One token set per `(tenant_id, connector_name)` |
| Examples | PostgreSQL, MySQL, Snowflake | Gmail, Slack, GitHub, Jira, Linear |

DB connectors are already production-ready. This document focuses on **App Connectors**.

---

## How App Connectors Work (Overview)

```
User query: "Draft an email to Alice about Q1 results"
      │
      ▼
SupervisorNode (dynamic routing)
  reads tenant's app_connections → finds Gmail connected
  routes to → gmail_subgraph
      │
      ▼
AppExecutionNode("gmail")
  1. Loads tenant token from app_connections
  2. Launches Gmail MCP server (STDIO or SSE) with token injected as env var
  3. Calls session.list_tools() → discovers search_threads, create_draft, send_message, ...
  4. Runs LLM tool-use loop: LLM picks tools, node calls them, returns results
  5. Synthesizes final response
```

The key insight: **every app connector is an MCP server**. The `MCPConnector` in `connectors/mcp_adapter.py` already handles STDIO and SSE transports. App connectors plug into it by providing a manifest that says how to launch or reach the MCP server and what credentials to inject.

---

## Control Plane Schema

Two new tables are needed in `scripts/init_control_plane.py`:

```sql
-- Stores per-tenant OAuth tokens and API keys for connected apps
CREATE TABLE app_connections (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    connector   TEXT NOT NULL,            -- "gmail", "slack", "github"
    status      TEXT NOT NULL DEFAULT 'connected',  -- connected | error | disconnected
    credentials JSONB NOT NULL,           -- encrypted: {access_token, refresh_token, expires_at, ...}
    connected_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, connector)
);
CREATE INDEX idx_app_connections_tenant ON app_connections(tenant_id);

-- User-defined agents (phase 2)
CREATE TABLE user_agents (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,          -- used by supervisor for routing
    instructions TEXT,                   -- extra system prompt injected into this agent
    connectors   TEXT[] NOT NULL,        -- ["gmail", "slack"] — tools are unioned at runtime
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_user_agents_tenant ON user_agents(tenant_id);
```

`credentials` must be encrypted before write. Use the tenant's encryption key derived from `CONNECTOR_MASTER_KEY` + `tenant_id` (AES-256-GCM). Never log or return this column to the frontend.

---

## AppConnectorManifest — The Contract

Every app connector registers a manifest dataclass. This is the only thing you need to write to add a new connector (aside from the MCP server itself):

```python
# src/axiom/connectors/apps/base.py
from dataclasses import dataclass, field
from typing import Literal, Optional

AuthType = Literal["oauth2_pkce", "api_key", "service_account", "none"]

@dataclass
class OAuth2Config:
    client_id_env: str          # env var name, e.g. "GMAIL_CLIENT_ID"
    client_secret_env: str
    auth_url: str               # e.g. "https://accounts.google.com/o/oauth2/v2/auth"
    token_url: str
    scopes: list[str]
    redirect_port: int = 8765   # local loopback port for CLI flow

@dataclass
class MCPServerSpec:
    transport: Literal["stdio", "sse"]
    # STDIO: launch a local process
    command: Optional[str] = None   # e.g. "npx"
    args: Optional[list[str]] = None  # e.g. ["-y", "@modelcontextprotocol/server-gmail"]
    env_token_key: Optional[str] = None  # env var name to inject access_token into
    # SSE: connect to a remote URL
    url_template: Optional[str] = None  # e.g. "https://mcp.example.com/{tenant_id}/sse"

@dataclass
class AppConnectorManifest:
    name: str                   # machine name, used as routing key: "gmail"
    display_name: str           # "Gmail"
    description: str            # shown to supervisor LLM for routing decisions
    categories: list[str]       # ["email", "communication"]
    auth_type: AuthType
    oauth2: Optional[OAuth2Config] = None
    api_key_env: Optional[str] = None  # for auth_type="api_key"
    mcp_server: MCPServerSpec = field(default_factory=MCPServerSpec)
    token_refresh_margin_seconds: int = 300  # refresh when < 5 min left
```

---

## Gmail Manifest (Reference Implementation)

```python
# src/axiom/connectors/apps/gmail.py
from axiom.connectors.apps.base import AppConnectorManifest, OAuth2Config, MCPServerSpec

GMAIL_MANIFEST = AppConnectorManifest(
    name="gmail",
    display_name="Gmail",
    description=(
        "Read, search, draft, label, and send emails via Gmail. "
        "Route here for any task involving email: inbox queries, drafting messages, "
        "checking threads, or sending replies."
    ),
    categories=["email", "communication"],
    auth_type="oauth2_pkce",
    oauth2=OAuth2Config(
        client_id_env="GMAIL_CLIENT_ID",
        client_secret_env="GMAIL_CLIENT_SECRET",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.labels",
        ],
    ),
    mcp_server=MCPServerSpec(
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-gmail"],
        env_token_key="GMAIL_OAUTH_TOKEN",
    ),
)
```

---

## AppConnectorFactory

Mirrors `ConnectorFactory` but for app connectors. Manages per-tenant MCP sessions with LRU eviction.

```python
# src/axiom/connectors/apps/factory.py

class AppConnectorFactory:
    _manifests: dict[str, AppConnectorManifest] = {}
    _sessions: OrderedDict[str, ClientSession] = OrderedDict()
    MAX_SESSIONS = 50

    @classmethod
    def register(cls, manifest: AppConnectorManifest):
        cls._manifests[manifest.name] = manifest

    @classmethod
    def get_manifest(cls, name: str) -> AppConnectorManifest:
        return cls._manifests[name]

    @classmethod
    def all_manifests(cls) -> list[AppConnectorManifest]:
        return list(cls._manifests.values())

    @classmethod
    async def get_session(cls, connector_name: str, tenant_id: str) -> ClientSession:
        """Returns a live MCP session, launching/connecting the server if needed."""
        key = f"{tenant_id}:{connector_name}"
        if key in cls._sessions:
            # LRU promote
            session = cls._sessions.pop(key)
            cls._sessions[key] = session
            return session

        if len(cls._sessions) >= cls.MAX_SESSIONS:
            _, old = cls._sessions.popitem(last=False)
            # close old session

        manifest = cls._manifests[connector_name]
        creds = await TokenStore.load(tenant_id, connector_name)
        creds = await TokenStore.maybe_refresh(manifest, creds)

        session = await _launch_mcp_session(manifest, creds)
        cls._sessions[key] = session
        return session

    @classmethod
    async def list_tools(cls, connector_name: str, tenant_id: str) -> list[Tool]:
        session = await cls.get_session(connector_name, tenant_id)
        result = await session.list_tools()
        return result.tools

    @classmethod
    async def call_tool(cls, connector_name: str, tenant_id: str, tool_name: str, args: dict) -> Any:
        session = await cls.get_session(connector_name, tenant_id)
        return await session.call_tool(tool_name, arguments=args)
```

---

## Auth Flow — OAuth2 PKCE (CLI)

The CLI connect flow for any OAuth2 app:

```
$ uv run python -m axiom.api.cli connect gmail

[axiom] Opening Gmail authorization...
[axiom] Visit this URL (or it will open automatically):

  https://accounts.google.com/o/oauth2/v2/auth?client_id=...&scope=...&redirect_uri=http://localhost:8765/callback&response_type=code&code_challenge=...

[axiom] Waiting for authorization (port 8765)...
[axiom] ✓ Authorization received.
[axiom] ✓ Gmail connected for tenant: my-workspace
```

Internally:
1. Generate PKCE `code_verifier` + `code_challenge` (S256).
2. Start a local HTTP server on `redirect_port` to catch the callback.
3. Open the browser (or print the URL if headless).
4. On callback, exchange `code` + `code_verifier` for `{access_token, refresh_token, expires_in}`.
5. Encrypt credentials and write to `app_connections`.
6. Close the local server.

For **API key** connectors:
```
$ uv run python -m axiom.api.cli connect linear --api-key lin_api_...
[axiom] ✓ Linear connected for tenant: my-workspace
```

No browser, no loopback — just encrypt and store.

---

## Dynamic Supervisor

The current `SupervisorNode` is hardcoded. The scalable version reads connected apps at runtime:

```python
class SupervisorNode:
    async def __call__(self, state: GlobalAgentState) -> dict:
        tenant_id = state["tenant_id"]

        # Always available
        agents = [
            "- SQL_AGENT: Query databases, run analytics, build charts. "
            "Route all data/metrics questions here."
        ]

        # Dynamically list connected apps
        connected = await AppConnectorFactory.get_connected_for_tenant(tenant_id)
        for manifest in connected:
            agents.append(f"- {manifest.name.upper()}_AGENT: {manifest.description}")

        # User-defined agents
        user_agents = await UserAgentStore.list(tenant_id)
        for ua in user_agents:
            agents.append(f"- {ua.name.upper()}_AGENT: {ua.description}")

        prompt = f"""
You are the Master Orchestrator for Axiom Brain.
Route the user query to the most appropriate agent.

### AVAILABLE AGENTS:
{chr(10).join(agents)}

### USER QUERY:
"{state['question']}"

Respond strictly with JSON: {{"next_agent": "<AGENT_NAME>"}}
"""
        # ... LLM call, parse JSON, return {"next_agent": ...}
```

The router function `_route_supervisor` does a case-insensitive match: `GMAIL_AGENT` → `gmail_subgraph`.

---

## Generic AppExecutionNode

One node handles all app connectors. No more per-connector node files:

```python
class AppExecutionNode:
    """Generic tool-use execution node for any MCP-backed app connector."""

    def __init__(self, connector_name: str):
        self.connector_name = connector_name

    async def __call__(self, state: AppAgentState) -> dict:
        tenant_id = state["tenant_id"]
        question = state["question"]

        tools = await AppConnectorFactory.list_tools(self.connector_name, tenant_id)
        tool_schemas = [t.inputSchema for t in tools]

        # Tool-use loop (max 5 rounds)
        messages = [{"role": "user", "content": question}]
        tool_results = []

        for _ in range(5):
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=messages,
                tools=tool_schemas,
            )
            choice = response.choices[0]
            if choice.finish_reason != "tool_calls":
                break
            for tc in choice.message.tool_calls:
                result = await AppConnectorFactory.call_tool(
                    self.connector_name, tenant_id, tc.function.name,
                    json.loads(tc.function.arguments)
                )
                tool_results.append({"tool": tc.function.name, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

        return {
            "mcp_tool_results": tool_results,
            "response_text": choice.message.content or "",
        }
```

---

## Adding a New Connector — 5 Steps

> This is the only thing you need to do to add a new app integration.

**Step 1 — Create the manifest file**
```
src/axiom/connectors/apps/<name>.py
```
Define one `AppConnectorManifest` instance following the Gmail reference above.

**Step 2 — Register it at startup**
In `src/axiom/connectors/apps/__init__.py`:
```python
from axiom.connectors.apps.factory import AppConnectorFactory
from axiom.connectors.apps.gmail import GMAIL_MANIFEST
from axiom.connectors.apps.slack import SLACK_MANIFEST  # new

AppConnectorFactory.register(GMAIL_MANIFEST)
AppConnectorFactory.register(SLACK_MANIFEST)
```

**Step 3 — Add a subgraph in `build_graph()`**
```python
for manifest in AppConnectorFactory.all_manifests():
    g = StateGraph(AppAgentState)
    g.add_node("execute", AppExecutionNode(manifest.name))
    g.set_entry_point("execute")
    g.add_edge("execute", END)
    compiled = g.compile()
    main_graph.add_node(f"{manifest.name}_subgraph", compiled)
    main_graph.add_edge(f"{manifest.name}_subgraph", END)
```

The supervisor already routes `SLACK_AGENT` → `slack_subgraph` via the dynamic router.

**Step 4 — Add the MCP server package**
For STDIO-based servers, add the npm package to `package.json` or document the `npx -y` invocation in the manifest. For SSE-based remote servers, just provide the URL template.

**Step 5 — Add the CLI connect command**
The `connect` CLI command is generic — it reads the manifest and runs the appropriate auth flow. No new code needed unless the auth type is novel.

```bash
uv run python -m axiom.api.cli connect <connector-name>
uv run python -m axiom.api.cli disconnect <connector-name>
uv run python -m axiom.api.cli connections list
```

---

## User-Defined Agents

Users can compose custom agents from multiple connected apps without writing code:

```bash
# Create an agent that can read Gmail and post to Slack
uv run python -m axiom.api.cli agents create \
  --name "Email Digest" \
  --description "Summarize unread emails and post a daily digest to Slack" \
  --connectors gmail,slack \
  --instructions "Always limit summaries to 3 bullet points. Post to #general."
```

Under the hood this writes a `user_agents` row. At runtime, `AppExecutionNode` unions the tools from all listed connectors and injects the custom instructions.

The supervisor picks up user-defined agents the same way as built-in ones — it reads the `description` column and includes it in the routing prompt. No graph rebuild needed.

---

## Scalability Notes

**100+ connectors don't require 100 subgraph nodes.** The graph has one `AppExecutionNode` parametrized by `connector_name`. The supervisor routes to `{name}_subgraph`, and the subgraphs are built in a loop at startup. Memory cost is O(connected sessions), not O(registered manifests).

**Session pooling.** `AppConnectorFactory` keeps live MCP sessions in an LRU cache (cap 50). STDIO sessions launch a child process per tenant per connector; SSE sessions reuse HTTP connections. Eviction closes the session gracefully.

**Token refresh.** `TokenStore.maybe_refresh()` checks `expires_at - now() < refresh_margin`. If stale, it uses the `refresh_token` to get a new `access_token` and updates `app_connections` before returning the session. This is synchronous-on-demand, not a background loop.

**Multi-tenant isolation.** Each `app_connections` row is scoped to `(tenant_id, connector)`. No tenant can read another tenant's tokens. ABAC enforcement in `MCPHub.pep` applies to all tool calls going through the hub.

**Adding a new auth type.** Implement a new `connect_<auth_type>()` function in `src/axiom/auth/oauth.py` and handle it in the `AuthStrategy` dispatcher. The manifest declares `auth_type`; the factory delegates to the right strategy. Existing connectors are unaffected.

---

## Current State vs. Target State

| Component | Current | Target |
|---|---|---|
| `GmailExecutionNode` | LLM mockup, no real Gmail | Replace with `AppExecutionNode("gmail")` |
| `SupervisorNode` | Hardcoded 2 agents | Dynamic — reads `app_connections` |
| `app_connections` table | Does not exist | Add to `init_control_plane.py` |
| `AppConnectorManifest` | Does not exist | New: `connectors/apps/base.py` |
| `AppConnectorFactory` | Does not exist | New: `connectors/apps/factory.py` |
| `TokenStore` | Does not exist | New: `auth/token_store.py` |
| CLI `connect` command | Does not exist | Add to `axiom.api.cli` |
| Gmail MCP server | Not launched | `npx @modelcontextprotocol/server-gmail` |
| User-defined agents | Does not exist | Phase 2 after Gmail ships |

---

## Gmail — Immediate Next Steps

In order of dependency:

1. **`scripts/init_control_plane.py`** — add `app_connections` and `user_agents` tables.
2. **`src/axiom/auth/token_store.py`** — `load()`, `save()`, `maybe_refresh()` backed by `app_connections`.
3. **`src/axiom/connectors/apps/base.py`** — manifest dataclasses.
4. **`src/axiom/connectors/apps/gmail.py`** — `GMAIL_MANIFEST`.
5. **`src/axiom/connectors/apps/factory.py`** — `AppConnectorFactory` with LRU session cache.
6. **`src/axiom/auth/oauth.py`** — PKCE flow for CLI.
7. **`axiom.api.cli`** — `connect`, `disconnect`, `connections list` commands.
8. **`src/axiom/agent/nodes.py`** — `AppExecutionNode` replacing `GmailExecutionNode`.
9. **`src/axiom/agent/graph.py`** — loop-built subgraphs + dynamic supervisor.
10. **`src/axiom/agent/supervisor.py`** — dynamic routing from `app_connections`.
