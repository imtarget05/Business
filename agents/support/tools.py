"""Support Agent tools (Phase 3, Task 3.3).

Three tools:
- send_email_reply: SMTP send with DRY-RUN default (draft-only; real send behind flag).
- create_ticket: creates a simple ticket record.
- lookup_customer: CRUD-lite over a simple customers table.

All tools are org-scoped and use the async SQLAlchemy session.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from uuid import UUID

from packages.config.settings import get_settings
from packages.core.tools import Tool
from packages.database.models import Customer, Ticket, TicketStatus
from packages.database.session import AsyncSession, async_sessionmaker, get_session_factory


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    """Default session factory (uses production settings)."""
    return get_session_factory()


class SendEmailReplyTool(Tool):
    """Send an email reply via SMTP.

    DRY-RUN mode is DEFAULT (draft-only). Real send requires
    `email_send_enabled=True` in settings. YAGNI: no retries/queueing.
    """

    name = "send_email_reply"
    description = (
        "Send an email reply to a customer. DRY-RUN mode is default — "
        "returns the draft without sending unless email_send_enabled=True in settings."
    )
    schema = {
        "type": "object",
        "properties": {
            "to_email": {"type": "string", "format": "email"},
            "subject": {"type": "string"},
            "body_text": {"type": "string"},
            "body_html": {"type": "string"},
            "conversation_id": {"type": "string", "format": "uuid"},
        },
        "required": ["to_email", "subject", "body_text"],
    }

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        # session_factory accepted for DI consistency with other tools; unused currently
        self._session_factory = session_factory or _default_session_factory()

    async def run(self, arguments: dict[str, Any]) -> str:
        settings = get_settings()

        to_email = arguments["to_email"]
        subject = arguments["subject"]
        body_text = arguments["body_text"]
        body_html = arguments.get("body_html")
        conversation_id = arguments.get("conversation_id")

        # Build the email message
        msg = EmailMessage()
        msg["From"] = settings.email_from_address or "noreply@example.com"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        # DRY-RUN mode default
        if not settings.email_send_enabled:
            draft = {
                "mode": "DRY_RUN",
                "from": msg["From"],
                "to": to_email,
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html,
                "conversation_id": conversation_id,
            }
            return json.dumps(draft, ensure_ascii=False)

        # Real send (only when explicitly enabled)
        if not settings.email_smtp_host:
            raise RuntimeError(
                "email_send_enabled=True but email_smtp_host not configured"
            )

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port) as server:
            server.starttls(context=context)
            if settings.email_smtp_username and settings.email_smtp_password:
                server.login(settings.email_smtp_username, settings.email_smtp_password)
            server.send_message(msg)

        result = {
            "mode": "SENT",
            "from": msg["From"],
            "to": to_email,
            "subject": subject,
            "conversation_id": conversation_id,
        }
        return json.dumps(result, ensure_ascii=False)


class CreateTicketTool(Tool):
    """Create a support ticket record."""

    name = "create_ticket"
    description = "Create a new support ticket for a customer."
    schema = {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "format": "uuid"},
            "customer_id": {"type": "string", "format": "uuid"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "assignee_id": {"type": "string", "format": "uuid"},
        },
        "required": ["organization_id", "customer_id", "subject"],
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or _default_session_factory()

    async def run(self, arguments: dict[str, Any]) -> str:
        org_id = UUID(arguments["organization_id"])
        customer_id = UUID(arguments["customer_id"])
        subject = arguments["subject"]
        description = arguments.get("description")
        assignee_id = UUID(arguments["assignee_id"]) if arguments.get("assignee_id") else None

        async with self._session_factory() as session:
            # Verify customer exists and belongs to org
            customer = await session.get(Customer, customer_id)
            if customer is None or customer.organization_id != org_id:
                from packages.core.errors import NotFoundError

                raise NotFoundError(
                    f"Customer {customer_id} not found in organization {org_id}"
                )

            ticket = Ticket(
                organization_id=org_id,
                customer_id=customer_id,
                subject=subject,
                description=description,
                status=TicketStatus.open,
                assignee_id=assignee_id,
            )
            session.add(ticket)
            await session.flush()
            await session.commit()

            return json.dumps(
                {
                    "ticket_id": str(ticket.id),
                    "organization_id": str(org_id),
                    "customer_id": str(customer_id),
                    "subject": subject,
                    "status": ticket.status.value,
                },
                ensure_ascii=False,
            )


class LookupCustomerTool(Tool):
    """CRUD-lite operations on customers table.

    Operations: create, get, update, list, delete (soft via status if needed).
    All operations are org-scoped.
    """

    name = "lookup_customer"
    description = "Look up, create, update, or list customers (org-scoped)."
    schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["create", "get", "update", "list", "delete"],
            },
            "organization_id": {"type": "string", "format": "uuid"},
            "customer_id": {"type": "string", "format": "uuid"},
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
            "notes": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
        "required": ["operation", "organization_id"],
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or _default_session_factory()

    async def run(self, arguments: dict[str, Any]) -> str:
        operation = arguments["operation"]
        org_id = UUID(arguments["organization_id"])

        async with self._session_factory() as session:
            if operation == "create":
                return await self._create(session, org_id, arguments)
            elif operation == "get":
                return await self._get(session, org_id, arguments)
            elif operation == "update":
                return await self._update(session, org_id, arguments)
            elif operation == "list":
                return await self._list(session, org_id, arguments)
            elif operation == "delete":
                return await self._delete(session, org_id, arguments)
            else:
                from packages.core.errors import ValidationError

                raise ValidationError(f"Unknown operation: {operation}")

    async def _create(
        self, session: Any, org_id: UUID, args: dict[str, Any]
    ) -> str:
        email = args["email"]
        name = args["name"]
        notes = args.get("notes")

        # Check for duplicate email in org
        from sqlalchemy import select

        stmt = select(Customer).where(
            Customer.organization_id == org_id, Customer.email == email
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            from packages.core.errors import ValidationError

            raise ValidationError(
                f"Customer with email {email} already exists in organization {org_id}"
            )

        customer = Customer(
            organization_id=org_id,
            email=email,
            name=name,
            notes=notes,
        )
        session.add(customer)
        await session.flush()
        await session.commit()

        return json.dumps(
            {
                "customer_id": str(customer.id),
                "organization_id": str(org_id),
                "email": email,
                "name": name,
                "notes": notes,
            },
            ensure_ascii=False,
        )

    async def _get(
        self, session: Any, org_id: UUID, args: dict[str, Any]
    ) -> str:
        customer_id = args.get("customer_id")
        email = args.get("email")

        from sqlalchemy import select

        if customer_id:
            stmt = select(Customer).where(
                Customer.id == UUID(customer_id), Customer.organization_id == org_id
            )
        elif email:
            stmt = select(Customer).where(
                Customer.email == email, Customer.organization_id == org_id
            )
        else:
            raise ValueError("Either customer_id or email required for get operation")

        customer = (await session.execute(stmt)).scalar_one_or_none()
        if customer is None:
            from packages.core.errors import NotFoundError

            raise NotFoundError("Customer not found")

        return json.dumps(
            {
                "customer_id": str(customer.id),
                "organization_id": str(customer.organization_id),
                "email": customer.email,
                "name": customer.name,
                "notes": customer.notes,
                "created_at": customer.created_at.isoformat() if customer.created_at else None,
                "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
            },
            ensure_ascii=False,
        )

    async def _update(
        self, session: Any, org_id: UUID, args: dict[str, Any]
    ) -> str:
        customer_id = UUID(args["customer_id"])
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.organization_id != org_id:
            from packages.core.errors import NotFoundError

            raise NotFoundError(
                f"Customer {customer_id} not found in organization {org_id}"
            )

        if "name" in args:
            customer.name = args["name"]
        if "email" in args:
            # Check for duplicate
            from sqlalchemy import select

            stmt = select(Customer).where(
                Customer.organization_id == org_id,
                Customer.email == args["email"],
                Customer.id != customer_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                from packages.core.errors import ValidationError

                raise ValidationError(
                    f"Customer with email {args['email']} already exists in organization {org_id}"
                )
            customer.email = args["email"]
        if "notes" in args:
            customer.notes = args["notes"]

        await session.flush()
        await session.commit()

        return json.dumps(
            {
                "customer_id": str(customer.id),
                "organization_id": str(customer.organization_id),
                "email": customer.email,
                "name": customer.name,
                "notes": customer.notes,
            },
            ensure_ascii=False,
        )

    async def _list(
        self, session: Any, org_id: UUID, args: dict[str, Any]
    ) -> str:
        from sqlalchemy import select

        limit = args.get("limit", 20)
        offset = args.get("offset", 0)

        stmt = (
            select(Customer)
            .where(Customer.organization_id == org_id)
            .order_by(Customer.name)
            .limit(limit)
            .offset(offset)
        )
        customers = list((await session.execute(stmt)).scalars().all())

        return json.dumps(
            {
                "customers": [
                    {
                        "customer_id": str(c.id),
                        "email": c.email,
                        "name": c.name,
                        "notes": c.notes,
                    }
                    for c in customers
                ],
                "count": len(customers),
                "limit": limit,
                "offset": offset,
            },
            ensure_ascii=False,
        )

    async def _delete(
        self, session: Any, org_id: UUID, args: dict[str, Any]
    ) -> str:
        customer_id = UUID(args["customer_id"])
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.organization_id != org_id:
            from packages.core.errors import NotFoundError

            raise NotFoundError("Customer not found")

        await session.delete(customer)
        await session.commit()

        return json.dumps(
            {"deleted": True, "customer_id": str(customer_id)}, ensure_ascii=False
        )


def create_support_tools(
    session_factory: async_sessionmaker[AsyncSession] | None = None
) -> list[Tool]:
    """Factory function to create all support agent tools.

    Args:
        session_factory: Optional custom session factory for testing.
                         Defaults to production session factory.
    """
    return [
        SendEmailReplyTool(),
        CreateTicketTool(session_factory),
        LookupCustomerTool(session_factory),
    ]


__all__ = [
    "SendEmailReplyTool",
    "CreateTicketTool",
    "LookupCustomerTool",
    "create_support_tools",
]