"""Support Agent tools (Phase 3, Task 3.3).

Three tools:
- send_email_reply: SMTP send with DRY-RUN default (draft-only; real send behind flag).
- create_ticket: creates a simple ticket record.
- lookup_customer: CRUD-lite over a simple customers table.

All tools are org-scoped and use the async SQLAlchemy session.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from uuid import UUID

from packages.config.settings import get_settings
from packages.core.errors import ToolExecutionError
from packages.core.tools import Tool
from packages.database.models import Conversation, Customer, Ticket, TicketStatus
from packages.database.session import AsyncSession, async_sessionmaker, get_session_factory


class _OrgBoundTool(Tool):
    """Mixin for tools whose organization context is injected SERVER-SIDE.

    The LLM never supplies ``organization_id``. If it tries (or supplies one
    that differs from the bound principal), the call is rejected.
    """

    def __init__(self) -> None:
        self._bound_org: UUID | None = None

    def bind_organization(self, organization_id: UUID | str | None) -> None:
        """Called by the tool loop / route with the TaskContext's org id."""
        if organization_id is None:
            self._bound_org = None
        elif isinstance(organization_id, UUID):
            self._bound_org = organization_id
        else:
            self._bound_org = UUID(str(organization_id))

    def _resolve_org(self, arguments: dict[str, Any]) -> UUID:
        """Resolve the caller's org strictly from the server-side binding."""
        supplied = arguments.pop("organization_id", None)
        if supplied is not None and self._bound_org is not None and str(supplied) != str(
            self._bound_org
        ):
            raise ToolExecutionError(
                "organization mismatch: tool arguments may not specify an "
                "organization other than the authenticated caller"
            )
        if self._bound_org is None:
            raise ToolExecutionError(
                "no server-side organization context bound for this tool run"
            )
        return self._bound_org


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    """Default session factory (uses production settings)."""
    return get_session_factory()


class SendEmailReplyTool(_OrgBoundTool):
    """Send an email reply via SMTP.

    DRY-RUN mode is DEFAULT (draft-only). Real send requires
    `email_send_enabled=True` in settings. YAGNI: no retries/queueing.

    Recipient allowlist (send path only): when sending is enabled, ``to_email``
    must belong to a customer record in the bound organization, or appear in
    ``settings.email_recipient_allowlist``. Otherwise ToolExecutionError.
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
        super().__init__()
        self._session_factory = session_factory or _default_session_factory()

    def _smtp_send(self, msg: EmailMessage, host: str, port: int) -> None:
        context = ssl.create_default_context()
        settings = get_settings()
        with smtplib.SMTP(host, port) as server:
            server.starttls(context=context)
            if settings.email_smtp_username and settings.email_smtp_password:
                server.login(settings.email_smtp_username, settings.email_smtp_password)
            server.send_message(msg)

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

        # DRY-RUN mode default (allowlist not enforced on drafts)
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

        org_id = self._resolve_org(arguments)
        if not await self._recipient_allowed(org_id, to_email, conversation_id):
            raise ToolExecutionError(
                f"recipient {to_email!r} is not allowlisted for this "
                "organization's conversations"
            )

        # Blocking SMTP I/O must not stall the event loop.
        await asyncio.to_thread(
            self._smtp_send, msg, settings.email_smtp_host, settings.email_smtp_port
        )

        result = {
            "mode": "SENT",
            "from": msg["From"],
            "to": to_email,
            "subject": subject,
            "conversation_id": conversation_id,
        }
        return json.dumps(result, ensure_ascii=False)

    async def _recipient_allowed(
        self, org_id: UUID, to_email: str, conversation_id: Any
    ) -> bool:
        if to_email.lower() in {
            e.lower() for e in get_settings().email_recipient_allowlist
        }:
            return True
        # Recipient must be a customer record in this org (optionally tied to
        # the conversation's organization).
        from sqlalchemy import select

        async with self._session_factory() as session:
            stmt = select(Customer).where(
                Customer.organization_id == org_id,
                Customer.email == to_email,
            )
            customer = (await session.execute(stmt)).scalar_one_or_none()
        return customer is not None


class SendGmailReplyTool(_OrgBoundTool):
    """Send an email reply via Gmail API.

    DRY-RUN mode is DEFAULT (draft-only; returns draft without sending).
    Real send requires `gmail_send_enabled=True` in settings.
    Logs every attempt (draft or sent) to Google Sheets.

    Recipient allowlist (send path only): when sending is enabled, ``to_email``
    must belong to a customer record in the bound organization, or appear in
    ``settings.gmail_allowed_recipients``. Otherwise ToolExecutionError.
    """

    name = "send_gmail_reply"
    description = (
        "Send an email reply to a customer via Gmail API. DRY-RUN mode is default — "
        "returns the draft without sending unless gmail_send_enabled=True in settings. "
        "All attempts are logged to Google Sheets."
    )
    schema = {
        "type": "object",
        "properties": {
            "to_email": {"type": "string", "format": "email"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "conversation_id": {"type": "string", "format": "uuid"},
        },
        "required": ["to_email", "subject", "body"],
    }

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        dry_run: bool | None = None,
    ) -> None:
        super().__init__()
        self._session_factory = session_factory or _default_session_factory()
        # Per-tool dry_run flag overrides settings when explicitly provided.
        # Default is None meaning "use settings.gmail_send_enabled".
        self._dry_run = dry_run

    def _build_sheet_row(
        self,
        conversation_id: str | None,
        customer_email: str,
        body: str,
        action: str,
    ) -> list[str]:
        """Build a row for the Google Sheet log."""
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()
        return [
            timestamp,
            conversation_id or "",
            customer_email,
            body[:500],
            action,
        ]

    async def _log_to_sheet(self, row: list[str]) -> None:
        """Log a row to Google Sheets (fire-and-forget, errors don't block)."""
        try:
            from integrations.google_client import sheet_log_row

            await asyncio.to_thread(sheet_log_row, row)
        except Exception:
            # Sheet logging failures should not block the tool result
            pass

    async def run(self, arguments: dict[str, Any]) -> str:
        settings = get_settings()

        to_email = arguments["to_email"]
        subject = arguments["subject"]
        body = arguments["body"]
        conversation_id = arguments.get("conversation_id")

        # Determine dry-run mode: per-tool flag overrides settings
        dry_run = (
            self._dry_run if self._dry_run is not None else not settings.gmail_send_enabled
        )

        # DRY-RUN mode: return draft, still log to sheet
        if dry_run:
            draft = {
                "mode": "DRY_RUN",
                "to": to_email,
                "subject": subject,
                "body": body,
                "conversation_id": conversation_id,
            }
            # Log to sheet as draft
            row = self._build_sheet_row(
                conversation_id, to_email, body, "draft"
            )
            await self._log_to_sheet(row)
            return json.dumps(draft, ensure_ascii=False)

        # Real send path: check allowlist
        if not settings.google_refresh_token:
            raise RuntimeError(
                "gmail_send_enabled=True but google_refresh_token not configured"
            )
        if not settings.google_oauth_client_id:
            raise RuntimeError(
                "gmail_send_enabled=True but google_oauth_client_id not configured"
            )
        if not settings.google_oauth_client_secret:
            raise RuntimeError(
                "gmail_send_enabled=True but google_oauth_client_secret not configured"
            )
        if not settings.google_sheet_id:
            raise RuntimeError(
                "gmail_send_enabled=True but google_sheet_id not configured"
            )

        org_id = self._resolve_org(arguments)
        if not await self._recipient_allowed(org_id, to_email):
            raise ToolExecutionError(
                f"recipient {to_email!r} is not allowlisted for this "
                "organization's conversations"
            )

        # Send via Gmail API (blocking I/O offloaded to thread)
        from integrations.google_client import gmail_send

        await asyncio.to_thread(gmail_send, to_email, subject, body)

        # Log to sheet as sent
        row = self._build_sheet_row(
            conversation_id, to_email, body, "gmail_send"
        )
        await self._log_to_sheet(row)

        result = {
            "mode": "SENT",
            "to": to_email,
            "subject": subject,
            "conversation_id": conversation_id,
        }
        return json.dumps(result, ensure_ascii=False)

    async def _recipient_allowed(self, org_id: UUID, to_email: str) -> bool:
        """Check if recipient is allowed for this organization."""
        settings = get_settings()
        if to_email.lower() in {
            e.lower() for e in settings.gmail_allowed_recipients
        }:
            return True
        # Recipient must be a customer record in this org
        from sqlalchemy import select

        async with self._session_factory() as session:
            stmt = select(Customer).where(
                Customer.organization_id == org_id,
                Customer.email == to_email,
            )
            customer = (await session.execute(stmt)).scalar_one_or_none()
        return customer is not None


class CreateTicketTool(_OrgBoundTool):
    """Create a support ticket record."""

    name = "create_ticket"
    description = "Create a new support ticket for a customer."
    schema = {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "format": "uuid"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "assignee_id": {"type": "string", "format": "uuid"},
        },
        "required": ["customer_id", "subject"],
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        super().__init__()
        self._session_factory = session_factory or _default_session_factory()

    async def run(self, arguments: dict[str, Any]) -> str:
        org_id = self._resolve_org(arguments)
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


class LookupCustomerTool(_OrgBoundTool):
    """CRUD-lite operations on customers table.

    Operations: create, get, update, list, delete (soft via status if needed).
    All operations are org-scoped; the org comes from the server-side binding.
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
            "customer_id": {"type": "string", "format": "uuid"},
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
            "notes": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
        "required": ["operation"],
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        super().__init__()
        self._session_factory = session_factory or _default_session_factory()

    async def run(self, arguments: dict[str, Any]) -> str:
        operation = arguments["operation"]
        org_id = self._resolve_org(arguments)

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
        SendGmailReplyTool(session_factory),
        CreateTicketTool(session_factory),
        LookupCustomerTool(session_factory),
    ]


__all__ = [
    "SendEmailReplyTool",
    "SendGmailReplyTool",
    "CreateTicketTool",
    "LookupCustomerTool",
    "create_support_tools",
]