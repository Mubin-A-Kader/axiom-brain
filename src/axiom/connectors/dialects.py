import logging
from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class BaseDialect(ABC):
    """Abstract definition of a database's metadata capabilities."""
    
    @abstractmethod
    def get_list_tables_query(self) -> str:
        """SQL query to return a list of all user tables."""
        pass

    @abstractmethod
    def get_table_name_column(self) -> str:
        """The column name in the result set that contains the table name."""
        pass

    def get_describe_table_query(self, table_name: str) -> str:
        """Optional query to get DDL/Schema for a specific table."""
        return f"SELECT * FROM {table_name} LIMIT 0"

class PostgresDialect(BaseDialect):
    def get_list_tables_query(self) -> str:
        return "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
    
    def get_table_name_column(self) -> str:
        return "tablename"

class MySQLDialect(BaseDialect):
    def get_list_tables_query(self) -> str:
        return "SHOW TABLES"
    
    def get_table_name_column(self) -> str:
        return "name" # MySQL often returns 'Tables_in_dbname', name is a fallback

class SnowflakeDialect(BaseDialect):
    def get_list_tables_query(self) -> str:
        return "SHOW TABLES"
    
    def get_table_name_column(self) -> str:
        return "name"

class GenericDialect(BaseDialect):
    def get_list_tables_query(self) -> str:
        # Standard SQL
        return "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN ('INFORMATION_SCHEMA', 'SYS', 'pg_catalog')"
    
    def get_table_name_column(self) -> str:
        return "table_name"

class MongoDBDialect(BaseDialect):
    def get_list_tables_query(self) -> str:
        return ""  # MongoDB has no SQL list-tables; schema comes from get_schema()

    def get_table_name_column(self) -> str:
        return "name"


class DialectRegistry:
    """Registry of known database dialects."""
    _dialects: Dict[str, BaseDialect] = {
        "postgresql": PostgresDialect(),
        "mysql": MySQLDialect(),
        "snowflake": SnowflakeDialect(),
        "mongodb": MongoDBDialect(),
        "default": GenericDialect()
    }

    @classmethod
    def get_dialect(cls, db_type: str) -> BaseDialect:
        """Lookup dialect by type, fallback to Generic."""
        return cls._dialects.get(db_type.lower(), cls._dialects["default"])
