"""Swap chunk embedding index from IVFFlat to HNSW.

IVFFlat is an *approximate* index that buckets vectors into ``lists``
centroids and probes only ``ivfflat.probes`` (default 1) at query time.
On tiny tables this silently returns zero rows when the query's nearest
centroid misses the bucket holding a row — retrieval comes back empty.

HNSW is accurate across all table sizes with no lists/probes tuning, so
retrieval stops randomly returning nothing.

Revision ID: 0002_hnsw_index
Revises: 0001_initial
"""

from alembic import op

revision = "0002_hnsw_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_chunks_embedding_ivfflat", table_name="chunks")
    op.execute(
        """
        CREATE INDEX ix_chunks_embedding_hnsw
        ON chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.execute(
        """
        CREATE INDEX ix_chunks_embedding_ivfflat
        ON chunks USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )
