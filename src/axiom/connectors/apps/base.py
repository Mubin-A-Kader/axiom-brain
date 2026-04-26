from dataclasses import dataclass, field
from typing import Literal, Optional

AuthType = Literal["oauth2_pkce", "api_key", "service_account", "none"]


@dataclass
class OAuth2Config:
    client_id_env: str
    client_secret_env: str
    auth_url: str
    token_url: str
    scopes: list[str]
    redirect_port: int = 8765


@dataclass
class MCPServerSpec:
    transport: Literal["stdio", "sse"]
    command: Optional[str] = None
    args: Optional[list[str]] = None
    env_token_key: Optional[str] = None   # env var name the MCP server reads the token from
    url_template: Optional[str] = None    # for SSE: remote URL, may contain {tenant_id}


@dataclass
class AppConnectorManifest:
    name: str                 # machine name used as routing key, e.g. "gmail"
    display_name: str         # human-readable, e.g. "Gmail"
    description: str          # shown to the supervisor LLM for routing decisions
    categories: list[str]     # e.g. ["email", "communication"]
    auth_type: AuthType
    oauth2: Optional[OAuth2Config] = None
    api_key_env: Optional[str] = None     # for auth_type="api_key": env var name
    mcp_server: MCPServerSpec = field(default_factory=lambda: MCPServerSpec(transport="stdio"))
    token_refresh_margin_seconds: int = 300
