"""Retriever — pgvector cosine similarity.

We use SQLAlchemy + the pgvector ``cosine_distance`` operator. The
returned ``score`` is ``1 - cosine_distance`` so higher = more similar
(matching the convention used by the reranker and evals).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from axiom.config import get_settings
from axiom.core.metrics import rag_retrieval_duration_seconds
from axiom.db.models import Chunk, Document
from axiom.llm.client import get_llm_client


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    ordinal: int


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    k = top_k or settings.rag_top_k

    llm = get_llm_client()
    started = time.perf_counter()
    [embedding] = await llm.embed(model=settings.default_embedding_model, texts=[query])

    distance = Chunk.embedding.cosine_distance(embedding)
    stmt = (
        select(Chunk, Document, distance.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .order_by(distance)
        .limit(k)
    )
    rows = (await session.execute(stmt)).all()
    rag_retrieval_duration_seconds.observe(time.perf_counter() - started)

    return [
        RetrievedChunk(
            chunk_id=str(c.id),
            document_id=str(d.id),
            document_title=d.title,
            content=c.content,
            score=1.0 - float(dist),
            ordinal=c.ordinal,
        )
        for c, d, dist in rows
    ]
