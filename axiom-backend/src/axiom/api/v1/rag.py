"""RAG endpoints.

POST /v1/rag/ingest  — Chunk, embed, and store documents.
POST /v1/rag/query   — Retrieve + rerank + generate an answer.
"""

from fastapi import APIRouter, Depends

from axiom.api.deps import DBSession
from axiom.core.security import CurrentPrincipal, enforce_rate_limit
from axiom.rag.ingest import IngestRequest as _IngestReq, ingest_documents
from axiom.rag.pipeline import answer as rag_answer
from axiom.schemas.rag import (
    CitationOut,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    RAGAnswerOut,
)

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    body: IngestRequest,
    session: DBSession,
    _: CurrentPrincipal,
) -> IngestResponse:
    docs = await ingest_documents(
        session,
        [
            _IngestReq(title=d.title, text=d.text, source_uri=d.source_uri, meta=d.meta)
            for d in body.documents
        ],
    )
    return IngestResponse(
        document_ids=[str(d.id) for d in docs],
        chunks_created=sum(len(d.chunks) for d in docs),
    )


@router.post("/query", response_model=RAGAnswerOut)
async def query(
    body: QueryRequest,
    session: DBSession,
    _: CurrentPrincipal,
) -> RAGAnswerOut:
    result = await rag_answer(session, body.query, use_reranker=body.use_reranker)
    return RAGAnswerOut(
        answer=result.answer,
        citations=[
            CitationOut(
                index=c.index,
                document_id=c.document_id,
                document_title=c.document_title,
                chunk_id=c.chunk_id,
                score=c.score,
            )
            for c in result.citations
        ],
    )
