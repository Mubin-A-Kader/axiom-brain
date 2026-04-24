import logging
from typing import Dict, Optional
from axiom.connectors.mcp_adapter import MCPConnector

logger = logging.getLogger("mcp-registry")

class MCPConnectorRegistry:
    """
    Registry for reusable MCP connectors.
    Prevents connection churn by keeping SSE sessions alive.
    """
    def __init__(self):
        self._connectors: Dict[str, MCPConnector] = {}

    async def get_connector(self, source_id: str, db_url: str, config: dict) -> MCPConnector:
        """
        Retrieves an existing connected connector or creates a new one.
        """
        # Key by source and URL to ensure we reuse the right connection
        key = f"{source_id}:{db_url}"
        
        if key in self._connectors:
            connector = self._connectors[key]
            if connector._session:
                logger.info(f"Reusing existing MCP connection for {key}")
                return connector
        
        logger.info(f"Creating NEW MCP connection for {key}")
        connector = MCPConnector(source_id, db_url, config)
        await connector.connect()
        self._connectors[key] = connector
        return connector

    async def shutdown(self):
        """Closes all active sessions."""
        for key, connector in self._connectors.items():
            logger.info(f"Closing MCP connection for {key}")
            await connector.disconnect()
        self._connectors.clear()

# Global Registry instance for the worker
mcp_registry = MCPConnectorRegistry()
