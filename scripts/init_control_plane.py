import asyncio
import asyncpg
from axiom.config import settings

async def main():
    conn = await asyncpg.connect(settings.database_url)
    try:
        # Ensure tenants table and columns
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        
        await conn.execute("""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='owner_id') THEN
                    ALTER TABLE tenants ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'unknown';
                END IF;
            END $$;
        """)
        
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant_owner ON tenants(owner_id);")

        # Ensure data_sources table and columns
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS data_sources (
                source_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                db_url TEXT NOT NULL,
                db_type TEXT NOT NULL DEFAULT 'postgresql',
                mcp_config JSONB,
                custom_rules TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                error_message TEXT
            );
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant_sources ON data_sources(tenant_id);")

        # Ensure existing table has the new columns (Migration)
        await conn.execute("""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='data_sources' AND column_name='db_type') THEN
                    ALTER TABLE data_sources ADD COLUMN db_type TEXT NOT NULL DEFAULT 'postgresql';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='data_sources' AND column_name='mcp_config') THEN
                    ALTER TABLE data_sources ADD COLUMN mcp_config JSONB;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='data_sources' AND column_name='status') THEN
                    ALTER TABLE data_sources ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='data_sources' AND column_name='error_message') THEN
                    ALTER TABLE data_sources ADD COLUMN error_message TEXT;
                END IF;
            END $$;
        """)

        print("Control plane 'data_sources' table initialized and migrated successfully.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
