import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from axiom.connectors.direct.postgres import PostgresConnector

@pytest.mark.asyncio
async def test_postgres_connector_ssh_tunnel_logic():
    """Verify that PostgresConnector attempts to start a tunnel when configured."""
    source_id = "ssh_test"
    db_url = "postgresql://user:pass@remote-db:5432/dbname"
    config = {
        "ssh": {
            "host": "bastion.host",
            "username": "ssh_user",
            "private_key": "fake-key"
        }
    }
    
    connector = PostgresConnector(source_id, db_url, config)
    
    # Mock SSHTunnelForwarder and asyncpg pool
    with patch("axiom.connectors.base.SSHTunnelForwarder") as mock_tunnel_class:
        mock_tunnel = mock_tunnel_class.return_value
        mock_tunnel.local_bind_port = 12345
        
        with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
            await connector.connect()
            
            # Check tunnel was started
            mock_tunnel.start.assert_called_once()
            
            # Check pool was created with rewritten URL
            args, _ = mock_create_pool.call_args
            assert "127.0.0.1:12345" in args[0]
            assert "user:pass" in args[0]
            assert "dbname" in args[0]
            
            # Check disconnect stops tunnel
            await connector.disconnect()
            mock_tunnel.stop.assert_called_once()

@pytest.mark.asyncio
async def test_postgres_connector_no_ssh_logic():
    """Verify that PostgresConnector skips tunnel logic when NOT configured."""
    source_id = "no_ssh_test"
    db_url = "postgresql://user:pass@direct-db:5432/dbname"
    config = {}
    
    connector = PostgresConnector(source_id, db_url, config)
    
    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        await connector.connect()
        
        # Check pool was created with original URL
        args, _ = mock_create_pool.call_args
        assert args[0] == db_url
        assert "direct-db" in args[0]
