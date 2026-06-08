"""ORM models.

Schema overview::

    documents ─┐
               └── chunks (pgvector embedding column)

    conversations ── messages
                  └── agent_runs ── agent_steps

    eval_datasets ── eval_examples
    eval_runs    ── eval_results
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from axiom.db.base import Base, TimestampMixin

EMBEDDING_DIM = 1536  # OpenAI text-embedding-3-small


# --- RAG ---------------------------------------------------------------------


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(1024))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        # HNSW index — accurate at any table size (IVFFlat drops rows on
        # tiny tables because it only probes a subset of lists).
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --- Conversations / Agents --------------------------------------------------


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    principal_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user|assistant|tool|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # running|succeeded|failed
    outcome: Mapped[str | None] = mapped_column(String(64))
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


# --- Evals -------------------------------------------------------------------


class EvalDataset(Base, TimestampMixin):
    __tablename__ = "eval_datasets"

    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    examples: Mapped[list["EvalExample"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class EvalExample(Base, TimestampMixin):
    __tablename__ = "eval_examples"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eval_datasets.id", ondelete="CASCADE"),
        index=True,
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    dataset: Mapped[EvalDataset] = relationship(back_populates="examples")


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_datasets.id", ondelete="CASCADE")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class EvalResult(Base, TimestampMixin):
    __tablename__ = "eval_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    example_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_examples.id", ondelete="CASCADE")
    )
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    scores: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)
    passed: Mapped[bool] = mapped_column(default=False)
