"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-23

Phase 0 initial migration. Creates the pgvector extension and all core tables
from `packages.database.models` metadata (single source of truth). A
hand-expanded migration set will replace this once the schema stabilizes in
Phase 1.
"""

from __future__ import annotations

from alembic import op

from packages.database import models  # noqa: F401
from packages.database.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension (Neon supports it natively; local image ships it too)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    op.execute("DROP EXTENSION IF EXISTS vector")
