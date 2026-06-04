"""Tool registry.

A tool is a callable plus JSON schema for its arguments. Tools register
themselves at import time via the ``@tool`` decorator; the agent
executor only sees ``Tool`` objects and their schemas.

Safety note: every tool receives a ``ToolContext`` so it can enforce
per-principal authorization (e.g. scope a DB query to the caller). The
executor never calls a tool without a context.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from axiom.llm.client import ToolSpec


@dataclass(slots=True)
class ToolContext:
    principal_id: str
    request_id: str
    extras: dict[str, Any] = field(default_factory=dict)


ToolFn = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn

    def to_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        tools = self._tools.values() if names is None else (self._tools[n] for n in names)
        return [t.to_spec() for t in tools]

    def __contains__(self, name: str) -> bool:
        return name in self._tools


registry = ToolRegistry()


def tool(name: str, description: str, parameters: dict[str, Any]) -> Callable[[ToolFn], ToolFn]:
    """Decorator: register a coroutine as a tool."""

    def decorator(fn: ToolFn) -> ToolFn:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Tool '{name}' must be async.")
        registry.register(Tool(name=name, description=description, parameters=parameters, fn=fn))
        return fn

    return decorator
