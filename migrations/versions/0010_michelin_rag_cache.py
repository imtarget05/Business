"""Michelin RAG cache — local Vector/FTS store for verified answers.

Part of AI-Engineer point 2 (RAG + Vector DB, no hallucination) and point 3
(cost: serve repeat questions from local DB instead of re-calling web+LLM).

We use PostgreSQL full-text search with the `simple` config so Vietnamese text
is indexed verbatim (no English stopword stripping). The source URLs are stored
alongside the answer so every cached reply stays verifiable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLE = "michelin_facts"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("query_hash", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("source_urls", sa.JSON(), nullable=True),
        sa.Column(
            "search_vector",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # GIN index on a tsvector built with the 'simple' config (Vietnamese-safe).
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', coalesce(question,'') || ' ' || coalesce(answer_text,''))) STORED"
    )
    op.create_index(f"ix_{TABLE}_tsv", TABLE, ["tsv"], postgresql_using="gin")
    op.create_index(f"ix_{TABLE}_qhash", TABLE, ["query_hash"], unique=True)


def downgrade() -> None:
    op.drop_table(TABLE)
