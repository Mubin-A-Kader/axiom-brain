"""Ingestion pipeline.

Documents are chunked with a simple token-aware sliding window
(``tiktoken``), embedded in batches via the LLM client, and written
to Postgres in a single transaction.

Production hardening to layer on top:
- Pluggable parsers per MIME type (PDF, HTML, Markdown, code).
- Recursive header-aware splitter for Markdown / HTML.
- Idempotency: dedupe by content hash before re-embedding.
- Streaming ingest from object storage with backpressure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession

from axiom.config import get_settings
from axiom.core.logging import get_logger
from axiom.db.models import Chunk, Document
from axiom.llm.client import get_llm_client

log = get_logger(__name__)
_encoder = tiktoken.get_encoding("cl100k_base")


@dataclass(slots=True)
class IngestRequest:
    title: str
    text: str
    source_uri: str | None = None
    meta: dict | None = None


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Sliding-window token chunker.

    Returns at least one chunk even for tiny inputs.
    """
    if not text.strip():
        return []
    tokens = _encoder.encode(text)
    if len(tokens) <= chunk_size:
        return [text]
    step = max(1, chunk_size - overlap)
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        end = start + chunk_size
        chunks.append(_encoder.decode(tokens[start:end]))
        if end >= len(tokens):
            break
    return chunks


async def ingest_documents(
    session: AsyncSession, requests: Iterable[IngestRequest]
) -> list[Document]:
    settings = get_settings()
    llm = get_llm_client()

    docs: list[Document] = []
    for req in requests:
        pieces = chunk_text(req.text, settings.rag_chunk_size, settings.rag_chunk_overlap)
        if not pieces:
            continue
        embeddings = await llm.embed(model=settings.default_embedding_model, texts=pieces)
        doc = Document(title=req.title, source_uri=req.source_uri, meta=req.meta or {})
        doc.chunks = [
            Chunk(ordinal=i, content=p, embedding=e)
            for i, (p, e) in enumerate(zip(pieces, embeddings, strict=True))
        ]
        session.add(doc)
        docs.append(doc)

    await session.flush()
    log.info("rag.ingest.done", n_docs=len(docs), n_chunks=sum(len(d.chunks) for d in docs))
    return docs
