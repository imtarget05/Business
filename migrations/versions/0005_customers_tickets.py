"""add customers + tickets

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25

Adds `customers` and `tickets` tables for Phase 3 support agent tools.
Idempotent: guards against tables already existing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "customers"):
        op.create_table(
            "customers",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("email", sa.String(320), nullable=False, index=True),
            sa.Column("notes", sa.Text, nullable=True),
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
        # Unique constraint on (email, organization_id)
        op.create_index(
            "uq_customer_email_org", "customers", ["email", "organization_id"], unique=True
        )
        op.create_index(
            "ix_customer_org_name", "customers", ["organization_id", "name"]
        )

    if not _table_exists(bind, "tickets"):
        op.create_table(
            "tickets",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "customer_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("customers.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("subject", sa.String(512), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="open",
                index=True,
            ),
            sa.Column(
                "assignee_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
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
        op.create_index(
            "ix_ticket_org_status", "tickets", ["organization_id", "status"]
        )
        op.create_index("ix_ticket_customer", "tickets", ["customer_id"])


def downgrade() -> None:
    if _table_exists(op.get_bind(), "tickets"):
        op.drop_table("tickets")
    if _table_exists(op.get_bind(), "customers"):
        op.drop_table("customers")