"""Cross-encoder-style reranker using the LLM itself.

For each candidate chunk we ask the model to score relevance (0-10) for
the query. This is the simplest reranker that genuinely improves recall
over plain embedding similarity, and it's swappable: drop in a hosted
cross-encoder (Cohere Rerank, BGE) by replacing this module.
"""

from __future__ import annotations

import asyncio
import re

from axiom.config import get_settings
from axiom.llm.client import ChatMessage, get_llm_client
from axiom.rag.retriever import RetrievedChunk

_SCORE_RE = re.compile(r"\b([0-9]|10)\b")
_RERANK_SYSTEM = (
    "You score how relevant a passage is to a user's query. "
    "Reply with a single integer from 0 (irrelevant) to 10 (perfect). "
    "No prose, no explanation."
)


async def rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if not candidates:
        return []
    settings = get_settings()
    llm = get_llm_client()

    async def score_one(c: RetrievedChunk) -> tuple[RetrievedChunk, float]:
        result = await llm.complete(
            model=settings.default_model,
            system=_RERANK_SYSTEM,
            messages=[
                ChatMessage(
                    role="user",
                    content=f"Query: {query}\n\nPassage:\n{c.content}\n\nScore:",
                )
            ],
            temperature=0.0,
            max_tokens=4,
        )
        match = _SCORE_RE.search(result.text)
        score = float(match.group(1)) / 10.0 if match else 0.0
        return c, score

    scored = await asyncio.gather(*(score_one(c) for c in candidates))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[: settings.rag_rerank_top_k]
    # Preserve original chunk fields but overwrite score with reranker score.
    return [RetrievedChunk(**{**c.__dict__, "score": s}) for c, s in top]
