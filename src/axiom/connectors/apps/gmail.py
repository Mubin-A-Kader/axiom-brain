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
        command="sys.python",   # resolved to sys.executable at launch time
        args=["-m", "axiom.connectors.apps.gmail_mcp_server"],
        env_token_key="GMAIL_OAUTH_TOKEN",
    ),
)
