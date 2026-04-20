import logging
from collections import OrderedDict
from typing import Dict, Any, Optional, Type

from axiom.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

class ConnectorFactory:
    """Registry and LRU cache for database connectors and their pools."""
    
    _connectors: Dict[str, BaseConnector] = OrderedDict()
    _registry: Dict[str, Type[BaseConnector]] = {}
    MAX_CONNECTORS = 20  # LRU limit for active connectors/pools

    @classmethod
    def register(cls, db_type: str, connector_class: Type[BaseConnector]):
        """Register a new connector implementation."""
        cls._registry[db_type] = connector_class
        logger.info(f"Registered connector for db_type: {db_type}")

    @classmethod
    async def get_connector(
        cls, 
        source_id: str, 
        db_type: str, 
        db_url: str, 
        config: Optional[Dict[str, Any]] = None
    ) -> BaseConnector:
        """Get an existing connector from cache or create a new one."""
        if source_id in cls._connectors:
            # Move to end to mark as recently used (LRU)
            connector = cls._connectors.pop(source_id)
            cls._connectors[source_id] = connector
            return connector

        # Check if we need to evict the oldest connector
        if len(cls._connectors) >= cls.MAX_CONNECTORS:
            old_id, old_connector = cls._connectors.popitem(last=False)
            logger.info(f"Evicting connector pool for source: {old_id}")
            await old_connector.disconnect()

        # Create new connector instance
        if db_type not in cls._registry:
            # Lazy import to avoid circular dependencies and only load what's needed
            await cls._lazy_load_registry()

        if db_type not in cls._registry:
            raise ValueError(f"Unsupported database type: {db_type}")

        connector_class = cls._registry[db_type]
        connector = connector_class(source_id, db_url, config)
        await connector.connect()
        
        cls._connectors[source_id] = connector
        return connector

    @classmethod
    async def _lazy_load_registry(cls):
        """Register built-in connectors."""
        from axiom.connectors.direct.postgres import PostgresConnector
        cls.register("postgresql", PostgresConnector)
        
        try:
            from axiom.connectors.direct.mysql import MySQLConnector
            cls.register("mysql", MySQLConnector)
        except ImportError:
            logger.warning("MySQL dependencies not found. MySQLConnector disabled.")

        try:
            from axiom.connectors.mcp_adapter import MCPConnector
            cls.register("mcp", MCPConnector)
        except ImportError:
            logger.warning("MCP dependencies not found. MCPConnector disabled.")

    @classmethod
    async def get_dialect_info(cls, db_type: str) -> tuple[str, str]:
        """Get dialect name and instructions for a specific database type without connecting."""
        if db_type not in cls._registry:
            await cls._lazy_load_registry()
            
        if db_type not in cls._registry:
            logger.warning(f"Unsupported database type '{db_type}' for dialect info, falling back to postgresql")
            db_type = "postgresql"
            
        connector_class = cls._registry[db_type]
        # Instantiate a dummy connector just to read properties
        dummy = connector_class("dummy", "dummy://")
        return dummy.dialect_name, dummy.llm_prompt_instructions

    @classmethod
    async def shutdown(cls):
        """Shut down all active connectors."""
        for source_id, connector in cls._connectors.items():
            logger.info(f"Shutting down connector: {source_id}")
            await connector.disconnect()
        cls._connectors.clear()
