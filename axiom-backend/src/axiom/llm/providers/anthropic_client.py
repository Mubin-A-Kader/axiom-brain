"""Anthropic Messages API adapter.

Tool-use convention: Anthropic returns ``content`` as a list of blocks
where each block has ``type`` of ``text`` or ``tool_use``. We flatten
text blocks into a single string and surface ``tool_use`` blocks as
``ToolCall`` objects.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from axiom.config import get_settings
from axiom.core.exceptions import UpstreamError
from axiom.llm.client import (
    ChatMessage,
    CompletionResult,
    ToolCall,
    ToolSpec,
    TokenUsage,
)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception_type(Exception),
    )
    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> CompletionResult:
        api_messages = [_to_anthropic_message(m) for m in messages]
        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "",
                messages=api_messages,
                tools=[_to_anthropic_tool(t) for t in tools] if tools else [],
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError("Anthropic request failed.", cause=str(exc)) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        return CompletionResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else (resp.stop_reason or "stop"),
            usage=TokenUsage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            ),
            raw=resp.model_dump(),
        )

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        # Anthropic does not ship a first-party embedding model — delegate.
        from axiom.llm.providers.openai_client import OpenAIClient

        return await OpenAIClient().embed(model=model, texts=texts)


def _to_anthropic_message(m: ChatMessage) -> dict[str, Any]:
    if m.role == "tool":
        # In Anthropic, tool results live inside a "user" message as tool_result blocks.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }
            ],
        }
    return {"role": m.role, "content": m.content}


def _to_anthropic_tool(t: ToolSpec) -> dict[str, Any]:
    return {"name": t.name, "description": t.description, "input_schema": t.parameters}
