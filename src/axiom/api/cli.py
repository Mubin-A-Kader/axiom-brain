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
    ingest_parser.add_argument("--url", required=True, help="PostgreSQL connection string")
    
    args = parser.parse_args()
    
    if args.command == "ingest":
        asyncio.run(run_ingestion(args.tenant, args.url))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
