"""End-to-end RAG: retrieve → rerank → generate.

The output is a structured ``RAGAnswer`` carrying both the synthesized
text and the citations the model relied on, so the API can show
provenance to the user and evals can check faithfulness.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from axiom.config import get_settings
from axiom.core.logging import get_logger
from axiom.llm.client import ChatMessage, get_llm_client
from axiom.rag.reranker import rerank
from axiom.rag.retriever import RetrievedChunk, retrieve

log = get_logger(__name__)

_SYSTEM = (
    "You are a careful assistant. Answer the user's question using ONLY the "
    "provided context. If the answer is not in the context, say you don't "
    "know. Cite sources inline using the bracketed numbers shown next to "
    "each passage (e.g. [1], [2])."
)


@dataclass(slots=True)
class Citation:
    index: int
    document_id: str
    document_title: str
    chunk_id: str
    score: float


@dataclass(slots=True)
class RAGAnswer:
    answer: str
    citations: list[Citation]
    retrieval: list[RetrievedChunk]


def _format_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{i + 1}] (source: {c.document_title})\n{c.content}"
        for i, c in enumerate(chunks)
    )


async def answer(
    session: AsyncSession,
    query: str,
    *,
    use_reranker: bool = True,
) -> RAGAnswer:
    settings = get_settings()
    retrieved = await retrieve(session, query)
    chunks = await rerank(query, retrieved) if use_reranker else retrieved

    if not chunks:
        return RAGAnswer(
            answer="I don't have any source material relevant to that question.",
            citations=[],
            retrieval=[],
        )

    llm = get_llm_client()
    context = _format_context(chunks)
    result = await llm.complete(
        model=settings.default_model,
        system=_SYSTEM,
        messages=[
            ChatMessage(role="user", content=f"Context:\n{context}\n\nQuestion: {query}")
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    citations = [
        Citation(
            index=i + 1,
            document_id=c.document_id,
            document_title=c.document_title,
            chunk_id=c.chunk_id,
            score=c.score,
        )
        for i, c in enumerate(chunks)
    ]
    log.info("rag.answer", n_chunks=len(chunks), tokens_out=result.usage.output_tokens)
    return RAGAnswer(answer=result.text, citations=citations, retrieval=chunks)
