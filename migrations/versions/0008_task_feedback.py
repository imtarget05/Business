"""Task feedback table for learning loop (ADR-010)

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_feedback" in inspector.get_table_names():
        return
    op.create_table(
        "task_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=False, index=True),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rating", sa.String(8), nullable=True),
        sa.Column("corrected_capability", sa.String(128), nullable=True, index=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="api"),
        sa.Column("auto_critique", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("task_feedback")
