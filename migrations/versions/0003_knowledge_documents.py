"""add knowledge documents + chunks

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23

Adds `documents` and `document_chunks` tables for Phase 2 RAG.
Idempotent: guards against columns/tables already existing from 0001
auto-migration.

NOTE: HNSW/IVFFlat ANN indexes are intentionally DEFERRED to a future
migration (0004) — they require real data to tune parameters against and
provide no benefit on an empty table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 768  # Cloudflare @cf/baai/bge-base-en-v1.5


def _table_exists(conn, table_name: str) -> bool:
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns(table_name)}
    return column_name in columns


def _index_exists(conn, index_name: str) -> bool:
    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes("document_chunks")}
    return index_name in indexes


def upgrade() -> None:
    bind = op.get_bind()

    # --- documents table ---
    if not _table_exists(bind, "documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("title", sa.String(512), nullable=False),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("source_ref", sa.String(2048), nullable=True),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                default="pending",
                server_default="pending",
            ),
            sa.Column("chunk_count", sa.Integer, nullable=False, default=0, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    # --- document_chunks table ---
    if not _table_exists(bind, "document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "document_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("chunk_index", sa.Integer, nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("token_count", sa.Integer, nullable=True),
            sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
            sa.Column("metadata", sa.JSON, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        # Unique constraint on (document_id, chunk_index)
        op.create_unique_constraint(
            "uq_document_chunk_index", "document_chunks", ["document_id", "chunk_index"]
        )

    # --- HNSW index DEFERRED to migration 0004 (needs real data to tune) ---


def downgrade() -> None:
    if _table_exists(op.get_bind(), "document_chunks"):
        op.drop_table("document_chunks")

    if _table_exists(op.get_bind(), "documents"):
        op.drop_table("documents")
