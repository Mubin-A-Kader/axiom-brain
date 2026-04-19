import argparse
import asyncio
import sys

from axiom.api.onboard import run_ingestion

def main():
    parser = argparse.ArgumentParser(description="Axiom CLI: The Reasoning-as-Infrastructure Control Plane.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Onboard / Ingest Command
    ingest_parser = subparsers.add_parser("ingest", help="Onboard a new tenant database by inspecting its schema.")
    ingest_parser.add_argument("--tenant", required=True, help="Unique Tenant ID (e.g., 'acme_corp')")
    ingest_parser.add_argument("--source", required=True, help="Unique Source ID for this database (e.g., 'sales_db')")
    ingest_parser.add_argument("--url", default="", help="Database connection string (required for direct types)")
    ingest_parser.add_argument("--type", default="postgresql", choices=["postgresql", "mysql", "mcp"], help="Database type")
    ingest_parser.add_argument("--desc", default="", help="Description of what data this database contains")
    ingest_parser.add_argument("--mcp-command", help="Command to run the MCP server (required if type is 'mcp')")
    ingest_parser.add_argument("--mcp-args", help="JSON list of arguments for the MCP server")
    
    # Query Command
    query_parser = subparsers.add_parser("query", help="Ask a natural language question to your data sources.")
    query_parser.add_argument("question", help="The natural language question")
    query_parser.add_argument("--tenant", required=True, help="Tenant ID")
    query_parser.add_argument("--source", help="Optional: Specific Source ID to query (skips auto-routing)")
    
    args = parser.parse_args()
    
    if args.command == "ingest":
        from axiom.api.onboard import run_ingestion
        mcp_config = None
        if args.type == "mcp":
            if not args.mcp_command:
                print("Error: --mcp-command is required when type is 'mcp'")
                sys.exit(1)
            mcp_config = {
                "command": args.mcp_command,
                "args": json.loads(args.mcp_args) if args.mcp_args else []
            }
        elif not args.url:
            print("Error: --url is required for direct database types")
            sys.exit(1)

        asyncio.run(run_ingestion(args.tenant, args.source, args.url, args.type, args.desc, mcp_config))
    elif args.command == "query":
        from axiom.api.query import run_query
        asyncio.run(run_query(args.question, args.tenant, args.source))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
