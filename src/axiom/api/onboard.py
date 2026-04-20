import argparse
import asyncio
import json
import logging
from typing import Optional, Dict, Any

import asyncpg
from axiom.config import settings
from axiom.rag.schema import SchemaRAG
from axiom.connectors.factory import ConnectorFactory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_ingestion(
    tenant_id: str, 
    source_id: str, 
    db_url: str, 
    db_type: str = "postgresql",
    description: str = "",
    mcp_config: Optional[Dict[str, Any]] = None,
    custom_rules: Any = None
):
    logger.info(f"Connecting to {db_type} database for source {source_id} (Tenant: {tenant_id})...")
    
    # 1. Update status to 'syncing' immediately
    cp_conn = await asyncpg.connect(settings.database_url)
    try:
        custom_rules_str = json.dumps(custom_rules) if custom_rules and not isinstance(custom_rules, str) else custom_rules
        await cp_conn.execute("""
            INSERT INTO data_sources (source_id, tenant_id, name, description, db_url, db_type, mcp_config, custom_rules, status)
            VALUES ($1, $2, $1, $3, $4, $5, $6, $7, 'syncing')
            ON CONFLICT (source_id) DO UPDATE 
            SET status = 'syncing', error_message = NULL, custom_rules = EXCLUDED.custom_rules;
        """, source_id, tenant_id, description, db_url, db_type, json.dumps(mcp_config) if mcp_config else None, custom_rules_str if custom_rules_str else "")

        # 2. Get the appropriate connector
        connector = await ConnectorFactory.get_connector(source_id, db_type, db_url, mcp_config)
        
        # 3. Extract schema using the connector's dialect-specific logic
        schema = await connector.get_schema()
        if not schema:
            raise Exception(f"No schema information found for {source_id}")
            
        logger.info(f"Extracted {len(schema)} tables. Ingesting into ChromaDB...")
        rag = SchemaRAG()
        # Pass tenant_id to ingest for strict isolation
        rag.ingest(tenant_id, source_id, schema)
        
        # 4. Final update: mark as active
        await cp_conn.execute("""
            UPDATE data_sources SET status = 'active', error_message = NULL
            WHERE source_id = $1
        """, source_id)

        logger.info(f"Successfully onboarded source {source_id} for tenant: {tenant_id}")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to onboard source {source_id}: {error_msg}")
        # Mark as failed in DB
        await cp_conn.execute("""
            UPDATE data_sources SET status = 'failed', error_message = $1
            WHERE source_id = $2
        """, error_msg, source_id)
        raise e
    finally:
        await cp_conn.close()
        # Important: Close the connector session if it was MCP or just cleanup
        await ConnectorFactory.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Axiom CLI: Onboard a new tenant database.")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--type", default="postgresql")
    parser.add_argument("--desc", default="")
    
    args = parser.parse_args()
    asyncio.run(run_ingestion(args.tenant, args.source, args.url, args.type, args.desc))
