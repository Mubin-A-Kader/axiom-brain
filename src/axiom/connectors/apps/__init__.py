from axiom.connectors.apps.factory import AppConnectorFactory
from axiom.connectors.n8n.services import SERVICES
from axiom.connectors.apps.base import AppConnectorManifest, MCPServerSpec

# Automatically register all n8n services as App Connectors
for service_id, svc in SERVICES.items():
    # Only register services that should be app connectors
    # (e.g. we might want to exclude Google Sheets if we switch to DuckDB later, but for now we register all as MCP)
    manifest = AppConnectorManifest(
        name=f"n8n_{service_id}",
        display_name=svc.label,
        description=f"Connect to {svc.label} via an authenticated proxy. {svc.description}. To interact, use the `call_service_api` tool and provide the FULL native REST API URL for {svc.label} (e.g. 'https://api.service.com/v1/resource'). Authentication is handled automatically.",
        categories=[svc.category],
        auth_type="none", # Authentication is handled by n8n behind the scenes
        mcp_server=MCPServerSpec(
            transport="stdio",
            command="sys.python",
            args=["-m", "axiom.connectors.apps.n8n_proxy_server"],
            # the n8n_proxy_server expects N8N_WEBHOOK_URL which needs to be injected dynamically
            # Wait, the credentials/webhook URL are stored in `data_sources` table `mcp_config`.
            # How does get_session know to inject it?
        )
    )
    AppConnectorFactory.register(manifest)
