import argparse
import asyncio
import json
import logging
from typing import Optional, Dict, Any

import asyncpg
import openai
from axiom.config import settings
from axiom.rag.schema import SchemaRAG

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """You are a database documentation expert. Given a table's DDL and a sample of its data, write a concise 2-3 sentence description for a semantic search index.

Your description MUST:
- State what real-world entity or concept this table stores
- Call out any EAV/key-value patterns (e.g. if a column like `system_label`, `key`, `question_type`, or `answer_key` stores categories, list a few real example values from the sample)
- Mention how this table joins to related tables (foreign keys)
- Use plain English that a non-technical user's question could match against

DDL:
{ddl}

Sample rows (up to 5):
{sample}

Write ONLY the description. No preamble, no bullet points."""


# Columns that are likely EAV "key" columns — sample DISTINCT values for these
_EAV_KEY_COLUMNS = {"system_label", "key", "label", "question_type", "answer_key", "attribute", "field_name", "metric_name", "event_type", "category"}


def _get_content(response) -> str:
    """Safely extract string content from OpenAI/LiteLLM response."""
    content = response.choices[0].message.content
    if content is None:
        return ""
    if isinstance(content, list):
        # Handle content blocks
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        content_str = "".join(parts)
    else:
        content_str = str(content)
        
    import re
    content_str = re.sub(r"<think>.*?</think>", "", content_str, flags=re.DOTALL)
    return content_str


async def _generate_table_summary(
    client: openai.AsyncOpenAI,
    model: str,
    table_name: str,
    ddl: str,
    connector,
    sem: asyncio.Semaphore,
) -> tuple[str, list]:
    """Sample table data and ask the LLM for a rich semantic summary. Returns (summary, samples)."""
    async with sem:
        sample_text = "No sample available."
        sample_rows = []
        try:
            # Detect EAV key columns from the DDL and sample their DISTINCT values
            import re
            col_names = re.findall(r'"(\w+)"\s+(?:text|character varying)', ddl, re.IGNORECASE)
            eav_cols = [c for c in col_names if c.lower() in _EAV_KEY_COLUMNS]

            extra_samples = []
            for col in eav_cols[:2]:
                try:
                    r = await connector.execute_query(
                        f'SELECT DISTINCT "{col}" FROM {table_name} WHERE "{col}" IS NOT NULL ORDER BY "{col}" LIMIT 50'
                    )
                    vals = [str(row[0]) for row in r.get("rows", [])]
                    if vals:
                        extra_samples.append(f'DISTINCT "{col}" values: {", ".join(vals)}')
                except Exception:
                    pass

            # Regular row sample
            result = await connector.execute_query(f'SELECT * FROM {table_name} LIMIT 5')
            rows = result.get("rows", [])
            cols = result.get("columns", [])
            if rows and cols:
                sample_rows = [dict(zip(cols, row)) for row in rows[:5]]
                lines = [", ".join(str(v) for v in row) for row in rows[:5]]
                sample_text = "columns: " + ", ".join(cols) + "\n" + "\n".join(lines)
                if extra_samples:
                    sample_text += "\n\n" + "\n".join(extra_samples)
            elif extra_samples:
                sample_text = "\n".join(extra_samples)
        except Exception:
            pass

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": _SUMMARY_PROMPT.format(
                    ddl=ddl[:1500],      # cap DDL in prompt — avoid huge tables
                    sample=sample_text[:1000],
                )}],
                max_tokens=150,
                temperature=0.2,
            )
            return _get_content(resp).strip(), sample_rows
        except Exception as exc:
            logger.warning("Summary generation failed for %s: %s", table_name, exc)
            return f"Table containing {table_name} data.", sample_rows


async def enrich_schema_with_summaries(
    schema: dict,
    connector,
    model: str,
    concurrency: int = 5,
) -> dict:
    """Generate rich LLM summaries and store data samples for every table in the schema dict."""
    client = openai.AsyncOpenAI(
        base_url=f"{settings.litellm_url}/v1",
        api_key=settings.litellm_key,
    )
    sem = asyncio.Semaphore(concurrency)

    async def _enrich(table_name: str, meta: dict) -> tuple[str, str, list]:
        summary, samples = await _generate_table_summary(
            client, model, table_name, meta["ddl"], connector, sem
        )
        return table_name, summary, samples

    tasks = [_enrich(t, m) for t, m in schema.items()]
    results = await asyncio.gather(*tasks)

    for table_name, summary, samples in results:
        schema[table_name]["description"] = summary
        schema[table_name]["sample_data"] = samples

    return schema

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

        # 2. Get the appropriate connector — skip for app connectors (gmail, etc.)
        from axiom.connectors.factory import ConnectorFactory
        query_mode = await ConnectorFactory.get_query_mode(db_type)
        if query_mode is None:
            await cp_conn.execute("""
                UPDATE data_sources SET status = 'active', error_message = NULL
                WHERE source_id = $1
            """, source_id)
            logger.info(f"App connector '{db_type}' source {source_id} registered (no schema ingestion)")
            return

        connector = await ConnectorFactory.get_connector(source_id, db_type, db_url, mcp_config)

        # 3. Extract schema using the connector's dialect-specific logic
        schema = await connector.get_schema()
        if not schema:
            raise Exception(f"No schema information found for {source_id}")
            
        logger.info(f"Extracted {len(schema)} tables. Generating rich summaries...")
        llm_model = settings.llm_model
        schema = await enrich_schema_with_summaries(schema, connector, llm_model)

        logger.info(f"Ingesting {len(schema)} tables into ChromaDB...")
        rag = SchemaRAG()
        await rag.ingest(tenant_id, source_id, schema)
        
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
        from axiom.connectors.factory import ConnectorFactory
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
