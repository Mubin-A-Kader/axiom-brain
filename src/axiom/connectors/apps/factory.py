import asyncio
import logging
import os
import shutil
from collections import OrderedDict
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from axiom.connectors.apps.base import AppConnectorManifest

logger = logging.getLogger(__name__)

_SessionEntry = tuple[ClientSession, AsyncExitStack]


class AppConnectorFactory:
    """Registry and session pool for app (non-SQL) MCP connectors."""

    _manifests: dict[str, AppConnectorManifest] = {}
    _sessions: "OrderedDict[str, _SessionEntry]" = OrderedDict()
    MAX_SESSIONS = 50

    @classmethod
    def register(cls, manifest: AppConnectorManifest) -> None:
        cls._manifests[manifest.name] = manifest
        logger.info("Registered app connector: %s", manifest.name)

    @classmethod
    def get_manifest(cls, name: str) -> AppConnectorManifest:
        if name not in cls._manifests:
            raise ValueError(f"Unknown app connector: '{name}'. Register a manifest first.")
        return cls._manifests[name]

    @classmethod
    def all_manifests(cls) -> list[AppConnectorManifest]:
        return list(cls._manifests.values())

    @classmethod
    async def get_connected_for_tenant(cls, tenant_id: str) -> list[AppConnectorManifest]:
        """Returns manifests for apps the tenant has connected, in connection order."""
        from axiom.auth.token_store import list_connected
        rows = await list_connected(tenant_id)
        return [
            cls._manifests[r["connector"]]
            for r in rows
            if r["connector"] in cls._manifests and r["status"] == "connected"
        ]

    @classmethod
    async def get_session(cls, connector_name: str, tenant_id: str) -> ClientSession:
        """Returns a live MCP session, launching the server if needed (LRU-cached)."""
        key = f"{tenant_id}:{connector_name}"

        if key in cls._sessions:
            session, stack = cls._sessions.pop(key)
            cls._sessions[key] = (session, stack)
            return session

        if len(cls._sessions) >= cls.MAX_SESSIONS:
            _, (__, old_stack) = cls._sessions.popitem(last=False)
            try:
                await old_stack.aclose()
            except Exception:
                pass

        manifest = cls.get_manifest(connector_name)
        from axiom.auth.token_store import load, maybe_refresh
        creds = await load(tenant_id, connector_name)
        creds = await maybe_refresh(manifest, creds, tenant_id)

        session, stack = await _launch_session(manifest, creds)
        cls._sessions[key] = (session, stack)
        return session

    _TOOL_CALL_TIMEOUT = 30  # seconds per individual tool invocation

    @classmethod
    async def list_tools(cls, connector_name: str, tenant_id: str) -> list:
        session = await cls.get_session(connector_name, tenant_id)
        result = await asyncio.wait_for(session.list_tools(), timeout=cls._TOOL_CALL_TIMEOUT)
        return result.tools

    @classmethod
    async def call_tool(cls, connector_name: str, tenant_id: str, tool_name: str, args: dict) -> Any:
        session = await cls.get_session(connector_name, tenant_id)
        return await asyncio.wait_for(
            session.call_tool(tool_name, arguments=args),
            timeout=cls._TOOL_CALL_TIMEOUT,
        )

    @classmethod
    async def shutdown(cls) -> None:
        for _, (__, stack) in list(cls._sessions.items()):
            try:
                await stack.aclose()
            except Exception:
                pass
        cls._sessions.clear()


_SESSION_INIT_TIMEOUT = 15  # seconds to wait for MCP server to respond to initialize()


async def _launch_session(manifest: AppConnectorManifest, creds: dict) -> _SessionEntry:
    spec = manifest.mcp_server
    stack = AsyncExitStack()
    try:
        if spec.transport == "sse":
            url = spec.url_template or ""
            read, write = await stack.enter_async_context(sse_client(url))
        else:
            import sys
            raw_cmd = spec.command or ""
            # "sys.python" is a special token meaning the current Python interpreter
            if raw_cmd == "sys.python":
                command = sys.executable
            else:
                command = shutil.which(raw_cmd) or raw_cmd
            env = dict(os.environ)
            if spec.env_token_key and creds.get("access_token"):
                env[spec.env_token_key] = creds["access_token"]
            elif spec.env_token_key and creds.get("api_key"):
                env[spec.env_token_key] = creds["api_key"]

            params = StdioServerParameters(
                command=command,
                args=spec.args or [],
                env=env,
            )
            read, write = await stack.enter_async_context(stdio_client(params))

        session = await stack.enter_async_context(ClientSession(read, write))
        try:
            await asyncio.wait_for(session.initialize(), timeout=_SESSION_INIT_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"MCP server for '{manifest.name}' did not respond to initialize() "
                f"within {_SESSION_INIT_TIMEOUT}s. "
                "Check that the server process starts correctly and the OAuth token is valid."
            )
        logger.info("MCP session ready for connector: %s", manifest.name)
        return session, stack
    except Exception:
        await stack.aclose()
        raise
