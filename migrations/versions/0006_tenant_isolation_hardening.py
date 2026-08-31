"""tenant isolation + sequence uniqueness hardening (audit fix wave)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

- tasks.organization_id becomes NOT NULL (CASCADE on org delete).
- uq(conversation_id, sequence) on messages.
- uq(task_id, sequence) on task_steps.

Idempotent: constraint creation is guarded; the NOT NULL backfill deletes any
orphan task rows with a null organization_id before tightening the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _has_constraint(conn, name: str) -> bool:
    inspector = sa.inspect(conn)
    for table in ("messages", "task_steps"):
        if name in {u["name"] for u in inspector.get_unique_constraints(table)}:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()

    # Backfill: drop orphaned task rows before making organization_id NOT NULL.
    bind.execute(sa.text("DELETE FROM tasks WHERE organization_id IS NULL"))

    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "organization_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )

    conn = bind
    inspector = sa.inspect(conn)
    msg_uc = {u["name"] for u in inspector.get_unique_constraints("messages")}
    step_uc = {u["name"] for u in inspector.get_unique_constraints("task_steps")}

    if "uq_message_sequence" not in msg_uc:
        op.create_unique_constraint(
            "uq_message_sequence", "messages", ["conversation_id", "sequence"]
        )
    if "uq_task_step_sequence" not in step_uc:
        op.create_unique_constraint("uq_task_step_sequence", "task_steps", ["task_id", "sequence"])


def downgrade() -> None:
    op.drop_constraint("uq_task_step_sequence", "task_steps", type_="unique")
    op.drop_constraint("uq_message_sequence", "messages", type_="unique")
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "organization_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=True,
        )
