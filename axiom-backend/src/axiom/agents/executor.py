"""Agent executor.

Implements a bounded tool-use loop:

1. Take user message + system prompt + tool specs and call the LLM.
2. If the LLM emits ``tool_use`` blocks, execute each tool, append the
   tool results as ``tool`` messages, and loop.
3. Stop when the LLM emits a final text response, hits the iteration
   cap, or the wall-clock timeout fires.
4. Every iteration is appended to a trace structure persisted on the
   ``AgentRun`` row, so we have full replayability.

The loop also runs every user-facing input through the safety
``guard_input`` function, and every assistant output through
``guard_output``. A safety block terminates the run with
``outcome=safety_block``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from axiom.agents.tools import ToolContext, registry
# Import built-in tools to register them at module import.
from axiom.agents.tools import builtin  # noqa: F401
from axiom.config import get_settings
from axiom.core.exceptions import SafetyBlock
from axiom.core.logging import get_logger
from axiom.core.metrics import agent_iterations_total
from axiom.core.tracing import tracer
from axiom.llm.client import ChatMessage, get_llm_client
from axiom.safety.guardrails import guard_input, guard_output

log = get_logger(__name__)


@dataclass(slots=True)
class AgentInput:
    user_message: str
    system_prompt: str = (
        "You are AXIOM, an autonomous agent. Use the available tools when "
        "they help you answer accurately. Cite sources when you use them."
    )
    enabled_tools: list[str] | None = None  # None = all registered tools


@dataclass(slots=True)
class AgentStep:
    iteration: int
    kind: str  # "llm" | "tool"
    payload: dict[str, Any]
    duration_ms: float


@dataclass(slots=True)
class AgentResult:
    answer: str
    outcome: str  # answered | tool_error | max_iterations | timeout | safety_block
    iterations: int
    trace: list[AgentStep] = field(default_factory=list)


async def run_agent(
    *, input_: AgentInput, principal_id: str, request_id: str
) -> AgentResult:
    settings = get_settings()
    llm = get_llm_client()
    tool_ctx = ToolContext(principal_id=principal_id, request_id=request_id)

    # --- Input safety -------------------------------------------------------
    try:
        sanitized = await guard_input(input_.user_message)
    except SafetyBlock as exc:
        agent_iterations_total.labels("safety_block").inc()
        return AgentResult(answer=exc.message, outcome="safety_block", iterations=0)

    tool_specs = registry.specs(input_.enabled_tools)
    messages: list[ChatMessage] = [ChatMessage(role="user", content=sanitized)]
    trace: list[AgentStep] = []
    deadline = time.monotonic() + settings.agent_timeout_seconds

    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("agent.tools.enabled", len(tool_specs))
        for iteration in range(1, settings.agent_max_iterations + 1):
            if time.monotonic() > deadline:
                agent_iterations_total.labels("timeout").inc()
                return AgentResult(
                    answer="Agent run timed out.", outcome="timeout",
                    iterations=iteration - 1, trace=trace,
                )

            started = time.perf_counter()
            with tracer.start_as_current_span("agent.llm") as llm_span:
                llm_span.set_attribute("agent.iteration", iteration)
                result = await llm.complete(
                    model=settings.default_model,
                    system=input_.system_prompt,
                    messages=messages,
                    tools=tool_specs,
                    temperature=0.2,
                    max_tokens=1024,
                )
            trace.append(
                AgentStep(
                    iteration=iteration, kind="llm",
                    payload={
                        "text": result.text,
                        "tool_calls": [asdict(tc) for tc in result.tool_calls],
                        "finish_reason": result.finish_reason,
                        "usage": asdict(result.usage),
                    },
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            )

            if not result.tool_calls:
                # Output safety
                try:
                    safe_text = await guard_output(result.text)
                except SafetyBlock as exc:
                    agent_iterations_total.labels("safety_block").inc()
                    return AgentResult(
                        answer=exc.message, outcome="safety_block",
                        iterations=iteration, trace=trace,
                    )
                agent_iterations_total.labels("answered").inc()
                return AgentResult(
                    answer=safe_text, outcome="answered",
                    iterations=iteration, trace=trace,
                )

            # Append the assistant tool-use turn before tool results.
            messages.append(ChatMessage(role="assistant", content=result.text))

            for call in result.tool_calls:
                tool_started = time.perf_counter()
                try:
                    tool_obj = registry.get(call.name)
                    output: Any = await asyncio.wait_for(
                        tool_obj.fn(tool_ctx, call.arguments), timeout=30
                    )
                    output_str = _stringify(output)
                except KeyError:
                    output_str = f"Tool '{call.name}' is not available."
                except asyncio.TimeoutError:
                    output_str = f"Tool '{call.name}' timed out."
                except Exception as exc:  # noqa: BLE001
                    output_str = f"Tool '{call.name}' failed: {exc}"
                    log.warning("agent.tool_error", tool=call.name, error=str(exc))

                trace.append(
                    AgentStep(
                        iteration=iteration, kind="tool",
                        payload={"name": call.name, "args": call.arguments, "output": output_str},
                        duration_ms=(time.perf_counter() - tool_started) * 1000,
                    )
                )
                messages.append(
                    ChatMessage(role="tool", content=output_str, tool_call_id=call.id, name=call.name)
                )

        agent_iterations_total.labels("max_iterations").inc()
        return AgentResult(
            answer="Agent did not converge within the iteration budget.",
            outcome="max_iterations",
            iterations=settings.agent_max_iterations,
            trace=trace,
        )


def _stringify(value: Any) -> str:
    import json

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)
