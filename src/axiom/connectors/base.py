from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseConnector(ABC):
    """Abstract base class for all Axiom database connectors."""
    
    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        self.source_id = source_id
        self.db_url = db_url
        self.config = config or {}

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection or initialize the pool."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection or the pool."""
        pass

    @abstractmethod
    async def execute_query(self, sql: str) -> Dict[str, Any]:
        """Execute a SQL query and return columns and rows."""
        pass

    @abstractmethod
    async def get_schema(self) -> Dict[str, Any]:
        """Extract database schema (tables, columns, foreign keys)."""
        pass
