"""OpenAI Chat Completions + Embeddings adapter."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
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


class OpenAIClient:
    provider = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

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
        api_messages: list[dict[str, Any]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                api_messages.append(
                    {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
                )
            else:
                api_messages.append({"role": m.role, "content": m.content})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError("OpenAI request failed.", cause=str(exc)) from exc

        choice = resp.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments or "{}"),
                    )
                )

        return CompletionResult(
            text=msg.content or "",
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else (choice.finish_reason or "stop"),
            usage=TokenUsage(
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            ),
            raw=resp.model_dump(),
        )

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.embeddings.create(model=model, input=texts)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError("OpenAI embeddings failed.", cause=str(exc)) from exc
        return [d.embedding for d in resp.data]
