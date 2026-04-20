import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from axiom.connectors.direct.mysql import MySQLConnector

@pytest.mark.asyncio
async def test_mysql_connector_ssh_tunnel_logic():
    """Verify that MySQLConnector attempts to start a tunnel when configured."""
    source_id = "mysql_ssh_test"
    db_url = "mysql://user:pass@remote-db:3306/dbname"
    config = {
        "ssh": {
            "host": "bastion.host",
            "username": "ssh_user",
            "private_key": "fake-key"
        }
    }
    
    connector = MySQLConnector(source_id, db_url, config)
    
    # Mock SSHTunnelForwarder and aiomysql pool
    with patch("axiom.connectors.base.SSHTunnelForwarder") as mock_tunnel_class:
        mock_tunnel = mock_tunnel_class.return_value
        mock_tunnel.local_bind_port = 54321
        
        with patch("aiomysql.create_pool", new_callable=AsyncMock) as mock_create_pool:
            await connector.connect()
            
            # Check tunnel was started
            mock_tunnel.start.assert_called_once()
            
            # Check pool was created with rewritten URL args
            _, kwargs = mock_create_pool.call_args
            assert kwargs["host"] == "127.0.0.1"
            assert kwargs["port"] == 54321
            assert kwargs["user"] == "user"
            assert kwargs["db"] == "dbname"
            
            # Check disconnect stops tunnel
            await connector.disconnect()
            mock_tunnel.stop.assert_called_once()
