import argparse
import asyncio
import json
import sys
from dotenv import load_dotenv

load_dotenv()  # makes .env values available to os.environ.get() calls


def main():
    parser = argparse.ArgumentParser(
        description="Axiom CLI: The Reasoning-as-Infrastructure Control Plane."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── ingest ──────────────────────────────────────────────────────────────
    ingest_p = subparsers.add_parser(
        "ingest", help="Onboard a tenant database by inspecting its schema."
    )
    ingest_p.add_argument("--tenant", required=True)
    ingest_p.add_argument("--source", required=True)
    ingest_p.add_argument("--url", default="")
    ingest_p.add_argument(
        "--type", default="postgresql", choices=["postgresql", "mysql", "mongodb", "mcp"]
    )
    ingest_p.add_argument("--desc", default="")
    ingest_p.add_argument("--mcp-command")
    ingest_p.add_argument("--mcp-args")

    # ── query ───────────────────────────────────────────────────────────────
    query_p = subparsers.add_parser(
        "query", help="Ask a natural-language question to your data sources."
    )
    query_p.add_argument("question")
    query_p.add_argument("--tenant", required=True)
    query_p.add_argument("--source")

    # ── connect ─────────────────────────────────────────────────────────────
    connect_p = subparsers.add_parser(
        "connect", help="Connect an external app (Gmail, Slack, …) for a tenant."
    )
    connect_p.add_argument(
        "connector",
        help="Connector name, e.g. 'gmail'. Run 'axiom connections list' for options.",
    )
    connect_p.add_argument("--tenant", required=True, help="Tenant ID")
    connect_p.add_argument(
        "--api-key",
        help="API key (for connectors that use key-based auth instead of OAuth2).",
    )

    # ── disconnect ───────────────────────────────────────────────────────────
    disconnect_p = subparsers.add_parser(
        "disconnect", help="Remove a connected app for a tenant."
    )
    disconnect_p.add_argument("connector", help="Connector name, e.g. 'gmail'.")
    disconnect_p.add_argument("--tenant", required=True)

    # ── connections ──────────────────────────────────────────────────────────
    connections_p = subparsers.add_parser(
        "connections", help="List connected apps for a tenant."
    )
    connections_p.add_argument("--tenant", required=True)

    # ── dispatch ─────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if args.command == "ingest":
        mcp_config = None
        if args.type == "mcp":
            if not args.mcp_command:
                print("Error: --mcp-command is required when type is 'mcp'")
                sys.exit(1)
            mcp_config = {
                "command": args.mcp_command,
                "args": json.loads(args.mcp_args) if args.mcp_args else [],
            }
        elif not args.url:
            print("Error: --url is required for direct database types")
            sys.exit(1)
        from axiom.api.onboard import run_ingestion
        asyncio.run(
            run_ingestion(args.tenant, args.source, args.url, args.type, args.desc, mcp_config)
        )

    elif args.command == "query":
        from axiom.api.query import run_query
        asyncio.run(run_query(args.question, args.tenant, args.source))

    elif args.command == "connect":
        asyncio.run(_cmd_connect(args.connector, args.tenant, getattr(args, "api_key", None)))

    elif args.command == "disconnect":
        asyncio.run(_cmd_disconnect(args.connector, args.tenant))

    elif args.command == "connections":
        asyncio.run(_cmd_connections(args.tenant))

    else:
        parser.print_help()
        sys.exit(1)


# ── connect handler ──────────────────────────────────────────────────────────

async def _cmd_connect(connector_name: str, tenant_id: str, api_key: str | None) -> None:
    import axiom.connectors.apps  # ensure manifests are registered
    from axiom.connectors.apps.factory import AppConnectorFactory
    from axiom.auth.token_store import save

    try:
        manifest = AppConnectorFactory.get_manifest(connector_name)
    except ValueError:
        available = [m.name for m in AppConnectorFactory.all_manifests()]
        print(f"Unknown connector '{connector_name}'. Available: {', '.join(available)}")
        sys.exit(1)

    if manifest.auth_type == "api_key":
        if not api_key:
            api_key = input(f"[axiom] Enter API key for {manifest.display_name}: ").strip()
        from axiom.auth.oauth import connect_api_key
        credentials = connect_api_key(manifest, api_key)

    elif manifest.auth_type == "oauth2_pkce":
        from axiom.auth.oauth import connect_oauth2_pkce
        credentials = connect_oauth2_pkce(manifest)

    elif manifest.auth_type == "none":
        credentials = {}

    else:
        print(f"Unsupported auth type '{manifest.auth_type}' for connector '{connector_name}'.")
        sys.exit(1)

    await save(tenant_id, connector_name, credentials)
    print(f"[axiom] ✓ {manifest.display_name} connected for tenant: {tenant_id}")


# ── disconnect handler ───────────────────────────────────────────────────────

async def _cmd_disconnect(connector_name: str, tenant_id: str) -> None:
    from axiom.auth.token_store import remove
    await remove(tenant_id, connector_name)
    print(f"[axiom] Disconnected '{connector_name}' for tenant: {tenant_id}")


# ── connections list handler ─────────────────────────────────────────────────

async def _cmd_connections(tenant_id: str) -> None:
    from axiom.auth.token_store import list_connected
    rows = await list_connected(tenant_id)
    if not rows:
        print(f"No apps connected for tenant: {tenant_id}")
        return
    print(f"\nConnected apps for tenant '{tenant_id}':\n")
    for row in rows:
        connected_at = str(row["connected_at"])[:19]
        print(f"  {row['connector']:<20} {row['status']:<12} connected {connected_at}")
    print()


if __name__ == "__main__":
    main()
