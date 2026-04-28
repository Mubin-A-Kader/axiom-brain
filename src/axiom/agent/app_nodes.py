import json
import logging
from typing import Any

import openai

from axiom.agent.state import AppAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 5


def _mcp_tools_to_openai(tools: list) -> list[dict]:
    """Convert MCP Tool objects to the OpenAI function-calling schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def _extract_content(result: Any) -> str:
    """Pull text out of an MCP tool call result."""
    if hasattr(result, "content"):
        parts = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
        return "\n".join(parts) if parts else str(result)
    return str(result)


class AppExecutionNode:
    """
    Generic tool-use execution node for any MCP-backed app connector.
    Instantiated once per connector at graph-build time.
    """

    def __init__(self, connector_name: str) -> None:
        self.connector_name = connector_name
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: AppAgentState) -> dict:
        tenant_id = state.get("tenant_id", "")
        question = state.get("question", "")
        history = state.get("history_context", "")
        model = state.get("llm_model") or settings.llm_model

        from axiom.connectors.apps.factory import AppConnectorFactory

        try:
            tools = await AppConnectorFactory.list_tools(self.connector_name, tenant_id)
        except ValueError as exc:
            return {
                "app_error": str(exc),
                "response_text": str(exc),
                "mcp_tool_results": [],
            }

        if not tools:
            return {
                "app_error": f"No tools available for connector '{self.connector_name}'.",
                "response_text": f"The {self.connector_name} connector returned no tools.",
                "mcp_tool_results": [],
            }

        try:
            manifest = AppConnectorFactory.get_manifest(self.connector_name)
            sys_msg = f"""You are a specialized agent for {manifest.display_name}.

INSTRUCTIONS:
{manifest.description}

SEARCH STRATEGY (CRITICAL):
When a user asks you to find or search for something, DO NOT rely solely on strict API filters. Users make typos.
1. Try the API's native search first.
2. If no results are found, try searching with partial substrings.
3. IF STILL NOT FOUND, YOU MUST issue a request with NO search filters (e.g., list all recent files/items) to fetch the raw list. Then, manually inspect the returned JSON to find the closest semantic match or typo equivalent. DO NOT tell the user you couldn't find it until you have fetched the unfiltered list and checked it yourself!"""
        except Exception:
            sys_msg = f"You are a specialized agent for connecting to {self.connector_name}."

        openai_tools = _mcp_tools_to_openai(tools)
        messages: list[dict] = [{"role": "system", "content": sys_msg}]
        if history:
            messages.append({"role": "system", "content": f"Conversation so far:\n{history}"})
        messages.append({"role": "user", "content": question})

        tool_results: list[dict] = []
        final_text = ""

        for _ in range(_MAX_TOOL_ROUNDS):
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.0,
            )
            choice = response.choices[0]
            messages.append(choice.message.model_dump(exclude_none=True))

            if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                final_text = choice.message.content or ""
                break

            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                try:
                    result = await AppConnectorFactory.call_tool(
                        self.connector_name, tenant_id, tc.function.name, args
                    )
                    content = _extract_content(result)
                except Exception as exc:
                    content = f"Tool error: {exc}"
                    logger.warning("Tool '%s' failed: %s", tc.function.name, exc)

                tool_results.append({"tool": tc.function.name, "args": args, "result": content})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

        return {
            "mcp_tool_results": tool_results,
            "response_text": final_text,
            "app_error": None,
        }
