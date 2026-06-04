"""Evaluation metrics.

A metric is an async callable ``(predicted, expected, context) -> float``
returning a score in [0, 1]. Failures should return 0.0, never raise.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from axiom.config import get_settings
from axiom.llm.client import ChatMessage, get_llm_client

Metric = Callable[[str, str, dict[str, Any]], Awaitable[float]]


async def exact_match(predicted: str, expected: str, _ctx: dict[str, Any]) -> float:
    return float(predicted.strip().lower() == expected.strip().lower())


async def contains(predicted: str, expected: str, _ctx: dict[str, Any]) -> float:
    return float(expected.strip().lower() in predicted.strip().lower())


_JUDGE_SYSTEM = (
    "You are an impartial grader. Compare the PREDICTED answer to the "
    "REFERENCE answer for the given QUESTION. Output JSON with two fields: "
    '"score" (0.0-1.0) and "rationale" (one sentence). No other text.'
)
_JSON_SCORE = re.compile(r'"score"\s*:\s*([0-9]*\.?[0-9]+)')


async def llm_judge(predicted: str, expected: str, ctx: dict[str, Any]) -> float:
    """LLM-as-judge factuality / semantic equivalence."""
    settings = get_settings()
    llm = get_llm_client()
    question = ctx.get("question", "")
    result = await llm.complete(
        model=settings.default_model,
        system=_JUDGE_SYSTEM,
        messages=[
            ChatMessage(
                role="user",
                content=(
                    f"QUESTION:\n{question}\n\n"
                    f"REFERENCE:\n{expected}\n\n"
                    f"PREDICTED:\n{predicted}\n\n"
                    "Respond with JSON only."
                ),
            )
        ],
        temperature=0.0,
        max_tokens=128,
    )
    match = _JSON_SCORE.search(result.text)
    if not match:
        return 0.0
    try:
        return max(0.0, min(1.0, float(match.group(1))))
    except ValueError:
        return 0.0


# Faithfulness for RAG: does the answer stick to its citations?
async def rag_faithfulness(predicted: str, _expected: str, ctx: dict[str, Any]) -> float:
    citations: list[str] = ctx.get("citations_text", [])
    if not citations:
        return 0.0
    joined = "\n---\n".join(citations)
    settings = get_settings()
    llm = get_llm_client()
    result = await llm.complete(
        model=settings.default_model,
        system=(
            "You verify whether an answer is supported by its source context. "
            'Reply with JSON: {"score": 0.0-1.0, "rationale": "..."}.'
        ),
        messages=[
            ChatMessage(
                role="user",
                content=f"CONTEXT:\n{joined}\n\nANSWER:\n{predicted}\n\nJSON:",
            )
        ],
        temperature=0.0,
        max_tokens=128,
    )
    match = _JSON_SCORE.search(result.text)
    return float(match.group(1)) if match else 0.0


REGISTRY: dict[str, Metric] = {
    "exact_match": exact_match,
    "contains": contains,
    "llm_judge": llm_judge,
    "rag_faithfulness": rag_faithfulness,
}
