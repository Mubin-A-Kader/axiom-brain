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
                -- Handle legacy table if it exists with 'tenant_id' instead of 'id'
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='id') AND 
                   EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='tenant_id') THEN
                    ALTER TABLE tenants RENAME COLUMN tenant_id TO id;
                END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='name') THEN
                    ALTER TABLE tenants ADD COLUMN name TEXT NOT NULL DEFAULT 'Nexus Workspace';
                END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='owner_id') THEN
                    ALTER TABLE tenants ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'unknown';
                END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='created_at') THEN
                    ALTER TABLE tenants ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
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

        # App connections — per-tenant OAuth2 / API-key credentials for external apps
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS app_connections (
                id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                connector    TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'connected',
                credentials  TEXT NOT NULL,
                connected_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (tenant_id, connector)
            );
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_connections_tenant ON app_connections(tenant_id);"
        )

        # User-defined agents — compose multiple connectors into a named agent
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_agents (
                id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                name         TEXT NOT NULL,
                description  TEXT NOT NULL,
                instructions TEXT,
                connectors   TEXT[] NOT NULL DEFAULT '{}',
                created_at   TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_agents_tenant ON user_agents(tenant_id);"
        )

        # Lakes — named curated subsets of sources
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS lakes (
                id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_lakes_tenant ON lakes(tenant_id);")

        # Lake sources — junction table for lakes and data_sources
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS lake_sources (
                lake_id    TEXT NOT NULL REFERENCES lakes(id) ON DELETE CASCADE,
                source_id  TEXT NOT NULL REFERENCES data_sources(source_id) ON DELETE CASCADE,
                added_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (lake_id, source_id)
            );
        """)

        # Migration: If there are entries in data_lake but no lakes, create a "Default Lake"
        tenants_with_data = await conn.fetch("SELECT DISTINCT tenant_id FROM data_lake")
        for r in tenants_with_data:
            tid = r["tenant_id"]
            # Check if this tenant already has a lake
            has_lake = await conn.fetchval("SELECT id FROM lakes WHERE tenant_id = $1 LIMIT 1", tid)
            if not has_lake:
                lake_id = await conn.fetchval(
                    "INSERT INTO lakes (tenant_id, name, description) VALUES ($1, $2, $3) RETURNING id",
                    tid, "Default Lake", "Automatic migration of your existing data lake."
                )
                # Move sources
                await conn.execute(
                    "INSERT INTO lake_sources (lake_id, source_id) SELECT $1, source_id FROM data_lake WHERE tenant_id = $2",
                    lake_id, tid
                )

        print("Control plane tables initialized successfully.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
