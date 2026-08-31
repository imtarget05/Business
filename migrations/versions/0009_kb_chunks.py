"""Knowledge Base chunks (Second Brain) — full-text, no embeddings.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "kb_chunks" in inspector.get_table_names():
        return

    # Full-text search via a GENERATED tsvector column + GIN index.
    # No embedding model required — runs fully offline.
    op.execute(
        sa.text(
            """
            CREATE TABLE kb_chunks (
                id UUID PRIMARY KEY,
                doc_id UUID NOT NULL,
                source_path TEXT NOT NULL,
                title TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                search_vector tsvector
                    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                created_at TIMESTAMP NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX ix_kb_chunks_tsvector ON kb_chunks USING gin (search_vector)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS kb_chunks"))
