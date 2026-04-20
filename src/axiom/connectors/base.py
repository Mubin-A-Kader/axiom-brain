import asyncio
import io
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from sshtunnel import SSHTunnelForwarder

logger = logging.getLogger(__name__)

class BaseConnector(ABC):
    """Abstract base class for all Axiom database connectors."""
    
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
            parsed_url = urlparse(self.db_url)
            remote_host = parsed_url.hostname or "localhost"
            remote_port = parsed_url.port or (5432 if "postgres" in self.db_url else 3306)

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
            local_port = self._ssh_tunnel.local_bind_port
            netloc = f"{parsed_url.username}:{parsed_url.password}@127.0.0.1:{local_port}"
            new_url = urlunparse(parsed_url._replace(netloc=netloc))
            
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
