"""add task_steps correlation_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23

Adds a `correlation_id` column to `task_steps`, enabling full-path tracing of an
orchestrator run (Item 3). Guards against the column already existing: migration
0001 creates tables from live model metadata, so on a fresh database the column
may already be present.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("task_steps")}
    if "correlation_id" not in columns:
        op.add_column(
            "task_steps",
            sa.Column("correlation_id", sa.String(length=64), nullable=True),
        )
        op.create_index("ix_task_steps_correlation_id", "task_steps", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_task_steps_correlation_id", table_name="task_steps")
    op.drop_column("task_steps", "correlation_id")