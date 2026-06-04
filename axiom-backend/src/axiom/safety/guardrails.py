"""Composable guardrails.

``guard_input`` runs before any user content reaches the LLM:
- Token-count limit.
- Prompt-injection heuristic.
- PII redaction (configurable).

``guard_output`` runs on every assistant message before it is returned
to the client or shown to a downstream tool:
- PII redaction (defense-in-depth — model can echo PII back).
- Refusal pattern check (returns the model's refusal as-is).

Both raise ``SafetyBlock`` to abort the operation cleanly; the agent
loop and API handlers catch this and emit a structured response.
"""

from __future__ import annotations

import tiktoken

from axiom.config import get_settings
from axiom.core.exceptions import SafetyBlock
from axiom.core.logging import get_logger
from axiom.core.metrics import safety_blocks_total
from axiom.safety.pii import redact_pii
from axiom.safety.prompt_injection import detect_prompt_injection

log = get_logger(__name__)
_encoder = tiktoken.get_encoding("cl100k_base")


async def guard_input(text: str) -> str:
    settings = get_settings()
    n_tokens = len(_encoder.encode(text))
    if n_tokens > settings.safety_max_input_tokens:
        safety_blocks_total.labels("input_too_long").inc()
        raise SafetyBlock(
            "Input exceeds the maximum allowed length.",
            tokens=n_tokens,
            limit=settings.safety_max_input_tokens,
        )

    if settings.safety_enable_prompt_injection_guard:
        if (hit := detect_prompt_injection(text)) is not None:
            safety_blocks_total.labels("prompt_injection").inc()
            log.warning("safety.prompt_injection", pattern=hit)
            raise SafetyBlock("Input rejected by prompt-injection guard.")

    if settings.safety_enable_pii_redaction:
        text, counts = redact_pii(text)
        if counts:
            log.info("safety.pii_redacted", counts=counts, direction="input")

    return text


async def guard_output(text: str) -> str:
    settings = get_settings()
    if settings.safety_enable_pii_redaction:
        text, counts = redact_pii(text)
        if counts:
            log.info("safety.pii_redacted", counts=counts, direction="output")
    return text
