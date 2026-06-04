from pydantic import Field

from axiom.schemas import APIModel


class IngestDocument(APIModel):
    title: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1)
    source_uri: str | None = None
    meta: dict = Field(default_factory=dict)


class IngestRequest(APIModel):
    documents: list[IngestDocument] = Field(min_length=1, max_length=50)


class IngestResponse(APIModel):
    document_ids: list[str]
    chunks_created: int


class QueryRequest(APIModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    use_reranker: bool = True


class CitationOut(APIModel):
    index: int
    document_id: str
    document_title: str
    chunk_id: str
    score: float


class RAGAnswerOut(APIModel):
    answer: str
    citations: list[CitationOut]
