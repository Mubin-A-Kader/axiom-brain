import asyncio
import asyncpg
from axiom.config import settings

async def main():
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                db_url TEXT NOT NULL,
                custom_rules TEXT DEFAULT ''
            );
        """)
        print("Control plane 'tenants' table initialized successfully.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
