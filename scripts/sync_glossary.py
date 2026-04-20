import argparse
import asyncio
import json
import yaml
import asyncpg
from axiom.config import settings

async def sync_glossary(tenant_id: str, source_id: str, file_path: str):
    print(f"Reading glossary from {file_path}...")
    
    with open(file_path, 'r') as f:
        if file_path.endswith('.yaml') or file_path.endswith('.yml'):
            data = yaml.safe_load(f)
        else:
            data = json.load(f)

    # Extract metrics if nested under a 'metrics' key (standard YAML best practice)
    metrics = data.get('metrics', data) if isinstance(data, dict) else data

    if not isinstance(metrics, list):
        print("Error: Glossary must be a list of metrics or a dict with a 'metrics' list.")
        return

    print(f"Syncing {len(metrics)} metrics to source '{source_id}' for tenant '{tenant_id}'...")

    metrics_json = json.dumps(metrics)

    conn = await asyncpg.connect(settings.database_url)
    try:
        # Check if source exists
        exists = await conn.fetchval(
            "SELECT 1 FROM data_sources WHERE tenant_id = $1 AND source_id = $2",
            tenant_id, source_id
        )
        if not exists:
            print(f"Error: Source '{source_id}' not found for tenant '{tenant_id}'.")
            return

        await conn.execute(
            "UPDATE data_sources SET custom_rules = $1 WHERE tenant_id = $2 AND source_id = $3",
            metrics_json, tenant_id, source_id
        )
        print("Successfully synced semantic layer.")
    finally:
        await conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Axiom CLI: Sync Business Glossary from YAML/JSON.")
    parser.add_argument("--tenant", required=True, help="Tenant ID")
    parser.add_argument("--source", required=True, help="Source ID")
    parser.add_argument("--file", required=True, help="Path to glossary.yaml or glossary.json")

    args = parser.parse_args()
    asyncio.run(sync_glossary(args.tenant, args.source, args.file))
