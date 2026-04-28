import asyncio
import asyncpg
import json
from axiom.config import settings

async def check():
    conn = await asyncpg.connect(settings.database_url)
    try:
        tenants = await conn.fetch("SELECT * FROM tenants")
        print(f"TENANTS: {json.dumps([dict(t) for t in tenants], default=str)}")
        
        sources = await conn.fetch("SELECT source_id, tenant_id, name, db_type, status FROM data_sources")
        print(f"ALL_SOURCES: {json.dumps([dict(s) for s in sources], default=str)}")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
