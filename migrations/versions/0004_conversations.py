"""add conversations + messages

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25

Adds `conversations` and `messages` tables for Phase 3 support agent
multi-turn thread persistence.
Idempotent: guards against tables already existing.

NOTE: the ANN index deferred to "0004" by 0003's docstring was renamed to a
future revision; this migration only adds conversation persistence.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "conversations"):
        op.create_table(
            "conversations",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("channel", sa.String(32), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="open",
                index=True,
            ),
            sa.Column("subject", sa.String(512), nullable=True),
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

    if not _table_exists(bind, "messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("sequence", sa.Integer, nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column(
                "parent_message_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("tool_metadata", sa.JSON, nullable=True),
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


def downgrade() -> None:
    if _table_exists(op.get_bind(), "messages"):
        op.drop_table("messages")
    if _table_exists(op.get_bind(), "conversations"):
        op.drop_table("conversations")
