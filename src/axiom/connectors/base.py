import asyncio
import io
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from sshtunnel import SSHTunnelForwarder

logger = logging.getLogger(__name__)


class QueryMode(str, Enum):
    """
    Declares how a connector is queried inside the Data Lake worker pipeline.

    SQL        — any relational database that accepts SELECT statements.
                 (PostgreSQL, MySQL, Snowflake, BigQuery, Redshift, DuckDB, SQLite, …)
                 Adding a new SQL connector only requires subclassing BaseConnector and
                 registering with ConnectorFactory — no other changes needed.

    PIPELINE   — document / columnar stores with their own native query language.
                 (MongoDB aggregation pipeline, Cassandra CQL, …)
                 The LakeWorker uses dialect-specific LLM instructions and does NOT
                 validate output as SQL. The connector's execute_query() handles parsing.

    APP        — service connectors backed by tool-use rather than a query language.
                 (Slack, GitHub, Notion, Salesforce, …)
                 These are declared via AppConnectorManifest, not BaseConnector, and
                 are dispatched by AppLakeWorker (tool-call loop), not LakeWorker.
    """
    SQL = "sql"
    PIPELINE = "pipeline"
    APP = "app"


class BaseConnector(ABC):
    """Abstract base class for all Axiom database connectors."""

    # Every subclass declares its query mode. Defaults to SQL so new SQL connectors
    # (Snowflake, BigQuery, Redshift, DuckDB, …) work in lake fan-out with zero extra wiring.
    query_mode: ClassVar[QueryMode] = QueryMode.SQL

    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        self.source_id = source_id
        self.db_url = db_url
        self.config = config or {}
        self._ssh_tunnel: Optional[SSHTunnelForwarder] = None

    async def _start_ssh_tunnel(self) -> str:
        """
        Starts an SSH tunnel if configured and returns a modified db_url.
        Expects self.config['ssh'] to contain host, port, username, and optionally password or private_key.
        """
        ssh_config = self.config.get("ssh")
        if not ssh_config:
            return self.db_url

        try:
            from axiom.core.cleansing import safe_db_urlparse
            parsed = safe_db_urlparse(self.db_url)
            remote_host = parsed["hostname"] or "localhost"
            remote_port = parsed["port"] or (5432 if "postgres" in self.db_url else 3306)

            ssh_host = ssh_config.get("host")
            ssh_port = int(ssh_config.get("port", 22))
            ssh_user = ssh_config.get("username")
            ssh_pass = ssh_config.get("password")
            ssh_key_str = ssh_config.get("private_key")

            tunnel_kwargs: Dict[str, Any] = {
                "ssh_address_or_host": (ssh_host, ssh_port),
                "ssh_username": ssh_user,
                "remote_bind_address": (remote_host, remote_port),
            }

            if ssh_key_str:
                # sshtunnel/paramiko requires a PKey object when passing key content directly
                import paramiko
                key_io = io.StringIO(ssh_key_str)
                pkey = None
                
                # Try loading as various key types
                for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
                    try:
                        key_io.seek(0)
                        pkey = key_class.from_private_key(key_io)
                        logger.debug(f"Successfully loaded SSH key as {key_class.__name__}")
                        break
                    except Exception:
                        continue
                
                if pkey:
                    tunnel_kwargs["ssh_pkey"] = pkey
                else:
                    logger.warning("Failed to parse SSH private key content. Falling back to StringIO.")
                    tunnel_kwargs["ssh_pkey"] = io.StringIO(ssh_key_str)
            elif ssh_pass:
                tunnel_kwargs["ssh_password"] = ssh_pass

            logger.info(f"Starting SSH tunnel for {self.source_id} via {ssh_host}:{ssh_port}")
            
            # Start tunnel in a thread to avoid blocking the event loop
            self._ssh_tunnel = SSHTunnelForwarder(**tunnel_kwargs)
            await asyncio.to_thread(self._ssh_tunnel.start)

            # Rewrite URL to use the local tunnel port
            from urllib.parse import quote
            local_port = self._ssh_tunnel.local_bind_port
            
            user = parsed["username"]
            pwd = parsed["password"]
            user_part = f"{quote(user)}:{quote(pwd)}@" if user and pwd else f"{quote(user)}@" if user else ""
            
            new_url = f"{parsed['scheme']}://{user_part}127.0.0.1:{local_port}{parsed['path']}"
            
            logger.info(f"SSH tunnel started. Local port: {local_port}")
            return new_url

        except Exception as e:
            logger.error(f"Failed to start SSH tunnel for {self.source_id}: {e}")
            if self._ssh_tunnel:
                await asyncio.to_thread(self._ssh_tunnel.stop)
                self._ssh_tunnel = None
            raise e

    async def _stop_ssh_tunnel(self) -> None:
        """Stops the active SSH tunnel if one exists."""
        if self._ssh_tunnel:
            logger.info(f"Stopping SSH tunnel for {self.source_id}")
            await asyncio.to_thread(self._ssh_tunnel.stop)
            self._ssh_tunnel = None

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

    @property
    @abstractmethod
    def dialect_name(self) -> str:
        """The identifier used by sqlglot (e.g., 'postgres', 'mysql', 'snowflake')."""
        pass

    @property
    @abstractmethod
    def llm_prompt_instructions(self) -> str:
        """Specific instructions to inject into the LLM prompt for this database."""
        pass

    def build_query_prompt(self, question: str, schema_context: str, custom_rules: str, few_shot_examples: str, history_context: str) -> str:
        """Build the LLM prompt for query generation. SQL connectors use the default; override for other query languages."""
        return (
            f"You are a precise SQL expert.\n"
            f"Target database: {self.dialect_name.upper()}\n\n"
            f"### SCHEMA:\n{schema_context}\n\n"
            f"### BUSINESS GLOSSARY:\n{custom_rules or 'None'}\n\n"
            f"### EXAMPLES:\n{few_shot_examples or 'None'}\n\n"
            f"### HISTORY:\n{history_context or 'None'}\n\n"
            f"### DIALECT RULES:\n{self.llm_prompt_instructions}\n\n"
            f"Generate a SELECT query to answer: {question}\n"
            "Output ONLY the SQL inside <sql></sql> tags. SELECT only — no writes, no DDL."
        )

    def extract_query(self, llm_content: str) -> Optional[str]:
        """Extract the query from raw LLM output. Override for non-SQL connectors."""
        import re
        match = re.search(r"<sql>(.*?)</sql>", llm_content, re.DOTALL)
        query = match.group(1).strip() if match else llm_content.replace("```sql", "").replace("```", "").strip()
        return query if query.upper().startswith("SELECT") else None

    def is_read_only_query(self, query: str) -> bool:
        """Return True if the query is safe to execute. Override for non-SQL connectors."""
        return query.strip().upper().startswith("SELECT")

