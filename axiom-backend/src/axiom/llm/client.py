"""LLM provider abstraction (BYOM).

The platform speaks to a uniform ``LLMClient`` interface so callers
(RAG, agents, evals) are decoupled from any specific vendor. Adding a
new provider means writing a new adapter under ``providers/`` and
registering it in ``get_llm_client``.

Design choices
--------------
* All inputs/outputs are normalized to a tiny set of dataclasses
  (``ChatMessage``, ``ToolSpec``, ``CompletionResult``).
* Tool-use is first-class: the result either contains text *or* a list
  of ``ToolCall`` blocks the caller is responsible for executing and
  feeding back.
* Embeddings live on the same interface for symmetry; in practice we
  route them to OpenAI by default but any provider can implement them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from axiom.config import get_settings
from axiom.core.metrics import llm_request_duration_seconds, llm_tokens_total

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str
    # For tool-result messages
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class CompletionResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # stop | tool_use | length | safety
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    provider: str

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> CompletionResult: ...

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


# --- Registry / factory ------------------------------------------------------

_client_cache: dict[str, LLMClient] = {}


def get_llm_client(provider: str | None = None) -> LLMClient:
    settings = get_settings()
    provider = provider or settings.default_llm_provider
    if provider in _client_cache:
        return _client_cache[provider]

    if provider == "anthropic":
        from axiom.llm.providers.anthropic_client import AnthropicClient
        client: LLMClient = AnthropicClient()
    elif provider == "openai":
        from axiom.llm.providers.openai_client import OpenAIClient
        client = OpenAIClient()
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    _client_cache[provider] = client
    return client


# --- Metrics decorator -------------------------------------------------------


class MeteredLLM:
    """Thin wrapper that records latency + token counts to Prometheus."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.provider = inner.provider

    async def complete(self, **kwargs: Any) -> CompletionResult:
        model = kwargs["model"]
        started = time.perf_counter()
        try:
            result = await self._inner.complete(**kwargs)
        finally:
            llm_request_duration_seconds.labels(self.provider, model).observe(
                time.perf_counter() - started
            )
        llm_tokens_total.labels(self.provider, model, "in").inc(result.usage.input_tokens)
        llm_tokens_total.labels(self.provider, model, "out").inc(result.usage.output_tokens)
        return result

    async def embed(self, **kwargs: Any) -> list[list[float]]:
        return await self._inner.embed(**kwargs)
