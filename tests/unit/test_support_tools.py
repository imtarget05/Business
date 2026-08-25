"""Unit tests for support agent tools (Phase 3, Task 3.3).

Covers:
- send_email_reply: DRY-RUN mode default, real send behind flag
- create_ticket: creates ticket record with org/customer scoping
- lookup_customer: CRUD-lite operations (create, get, update, list, delete)
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.config.settings import get_settings
from packages.core.errors import NotFoundError, ValidationError
from packages.database import models
from packages.database.base import Base
from packages.database.models import Customer, Ticket, TicketStatus
from packages.database.session import get_session_factory, session_scope
from agents.support.tools import (
    SendEmailReplyTool,
    CreateTicketTool,
    LookupCustomerTool,
    create_support_tools,
)


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
async def test_session_factory():
    """Create a temporary SQLite database with all tables and return session factory."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture()
async def db(test_session_factory):
    """Create a session for direct database access in tests."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def other_org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
async def test_customer(db, org_id) -> Customer:
    """Create a test customer in the database."""
    customer = Customer(
        id=uuid.uuid4(),
        organization_id=org_id,
        email="test@example.com",
        name="Test Customer",
        notes="Test notes",
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


# ========================================================================
# SendEmailReplyTool tests
# ========================================================================


class TestSendEmailReplyTool:
    """Tests for send_email_reply tool."""

    @pytest.fixture()
    def tool(self) -> SendEmailReplyTool:
        return SendEmailReplyTool()

    @pytest.mark.asyncio
    async def test_dry_run_mode_default(self, tool):
        """DRY-RUN mode is default (email_send_enabled=False)."""
        # Ensure settings have email_send_enabled=False (default)
        settings = get_settings()
        assert settings.email_send_enabled is False

        result = await tool.run(
            {
                "to_email": "customer@example.com",
                "subject": "Test Subject",
                "body_text": "Test body",
                "conversation_id": str(uuid.uuid4()),
            }
        )

        data = json.loads(result)
        assert data["mode"] == "DRY_RUN"
        assert data["to"] == "customer@example.com"
        assert data["subject"] == "Test Subject"
        assert data["body_text"] == "Test body"
        assert "conversation_id" in data

    @pytest.mark.asyncio
    async def test_dry_run_with_html_body(self, tool):
        """DRY-RUN includes HTML body when provided."""
        result = await tool.run(
            {
                "to_email": "customer@example.com",
                "subject": "HTML Test",
                "body_text": "Plain text",
                "body_html": "<p>HTML <b>body</b></p>",
            }
        )

        data = json.loads(result)
        assert data["mode"] == "DRY_RUN"
        assert data["body_html"] == "<p>HTML <b>body</b></p>"

    @pytest.mark.asyncio
    async def test_real_send_requires_smtp_config(self, tool, monkeypatch):
        """Real send requires SMTP configuration."""
        # Enable send but don't configure SMTP
        monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
        monkeypatch.setenv("EMAIL_SMTP_HOST", "")
        # Clear cached settings
        get_settings.cache_clear()

        with pytest.raises(RuntimeError, match="email_smtp_host not configured"):
            await tool.run(
                {
                    "to_email": "customer@example.com",
                    "subject": "Test",
                    "body_text": "Test",
                }
            )

        # Restore defaults
        monkeypatch.delenv("EMAIL_SEND_ENABLED", raising=False)
        monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
        get_settings.cache_clear()


# ========================================================================
# CreateTicketTool tests
# ========================================================================


class TestCreateTicketTool:
    """Tests for create_ticket tool."""

    @pytest.fixture()
    def tool(self, test_session_factory) -> CreateTicketTool:
        return CreateTicketTool(session_factory=test_session_factory)

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, tool, db, org_id, test_customer):
        """Create a ticket successfully."""
        result = await tool.run(
            {
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
                "subject": "Billing issue",
                "description": "Customer reports incorrect charge",
            }
        )

        data = json.loads(result)
        assert "ticket_id" in data
        assert data["organization_id"] == str(org_id)
        assert data["customer_id"] == str(test_customer.id)
        assert data["subject"] == "Billing issue"
        assert data["status"] == "open"

        # Verify in database
        ticket = await db.get(Ticket, uuid.UUID(data["ticket_id"]))
        assert ticket is not None
        assert ticket.subject == "Billing issue"
        assert ticket.status == TicketStatus.open

    @pytest.mark.asyncio
    async def test_create_ticket_with_assignee(self, tool, db, org_id, test_customer):
        """Create a ticket with an assignee."""
        assignee_id = uuid.uuid4()
        result = await tool.run(
            {
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
                "subject": "Technical issue",
                "assignee_id": str(assignee_id),
            }
        )

        data = json.loads(result)
        assert data["subject"] == "Technical issue"

        ticket = await db.get(Ticket, uuid.UUID(data["ticket_id"]))
        assert ticket.assignee_id == assignee_id

    @pytest.mark.asyncio
    async def test_create_ticket_rejects_foreign_customer(self, tool, db, org_id, other_org_id):
        """Reject creating ticket with customer from different org."""
        # Create customer in other org
        other_customer = Customer(
            id=uuid.uuid4(),
            organization_id=other_org_id,
            email="other@example.com",
            name="Other Customer",
        )
        db.add(other_customer)
        await db.commit()

        with pytest.raises(NotFoundError, match="not found in organization"):
            await tool.run(
                {
                    "organization_id": str(org_id),
                    "customer_id": str(other_customer.id),
                    "subject": "Should fail",
                }
            )

    @pytest.mark.asyncio
    async def test_create_ticket_rejects_nonexistent_customer(self, tool, org_id):
        """Reject creating ticket with nonexistent customer."""
        with pytest.raises(NotFoundError, match="not found in organization"):
            await tool.run(
                {
                    "organization_id": str(org_id),
                    "customer_id": str(uuid.uuid4()),
                    "subject": "Should fail",
                }
            )


# ========================================================================
# LookupCustomerTool tests
# ========================================================================


class TestLookupCustomerTool:
    """Tests for lookup_customer tool (CRUD-lite)."""

    @pytest.fixture()
    def tool(self, test_session_factory) -> LookupCustomerTool:
        return LookupCustomerTool(session_factory=test_session_factory)

    # ----- create -----

    @pytest.mark.asyncio
    async def test_create_customer_success(self, tool, db, org_id):
        """Create a new customer."""
        result = await tool.run(
            {
                "operation": "create",
                "organization_id": str(org_id),
                "email": "new@example.com",
                "name": "New Customer",
                "notes": "VIP",
            }
        )

        data = json.loads(result)
        assert "customer_id" in data
        assert data["email"] == "new@example.com"
        assert data["name"] == "New Customer"
        assert data["notes"] == "VIP"

        # Verify in database
        customer = await db.get(Customer, uuid.UUID(data["customer_id"]))
        assert customer is not None
        assert customer.email == "new@example.com"

    @pytest.mark.asyncio
    async def test_create_customer_rejects_duplicate_email(self, tool, db, org_id, test_customer):
        """Reject creating customer with duplicate email in same org."""
        with pytest.raises(ValidationError, match="already exists"):
            await tool.run(
                {
                    "operation": "create",
                    "organization_id": str(org_id),
                    "email": test_customer.email,  # duplicate
                    "name": "Duplicate",
                }
            )

    @pytest.mark.asyncio
    async def test_create_customer_allows_same_email_different_org(
        self, tool, db, org_id, other_org_id, test_customer
    ):
        """Allow same email in different organization."""
        result = await tool.run(
            {
                "operation": "create",
                "organization_id": str(other_org_id),
                "email": test_customer.email,  # same email, different org
                "name": "Other Org Customer",
            }
        )

        data = json.loads(result)
        assert data["email"] == test_customer.email
        assert data["organization_id"] == str(other_org_id)

    # ----- get -----

    @pytest.mark.asyncio
    async def test_get_customer_by_id(self, tool, db, org_id, test_customer):
        """Get customer by ID."""
        result = await tool.run(
            {
                "operation": "get",
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
            }
        )

        data = json.loads(result)
        assert data["customer_id"] == str(test_customer.id)
        assert data["email"] == test_customer.email
        assert data["name"] == test_customer.name

    @pytest.mark.asyncio
    async def test_get_customer_by_email(self, tool, db, org_id, test_customer):
        """Get customer by email."""
        result = await tool.run(
            {
                "operation": "get",
                "organization_id": str(org_id),
                "email": test_customer.email,
            }
        )

        data = json.loads(result)
        assert data["customer_id"] == str(test_customer.id)
        assert data["email"] == test_customer.email

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, tool, org_id):
        """Get raises NotFoundError for non-existent customer."""
        with pytest.raises(NotFoundError, match="Customer not found"):
            await tool.run(
                {
                    "operation": "get",
                    "organization_id": str(org_id),
                    "customer_id": str(uuid.uuid4()),
                }
            )

    @pytest.mark.asyncio
    async def test_get_customer_rejects_foreign_org(self, tool, db, org_id, other_org_id, test_customer):
        """Get rejects customer from different org."""
        with pytest.raises(NotFoundError, match="Customer not found"):
            await tool.run(
                {
                    "operation": "get",
                    "organization_id": str(other_org_id),
                    "customer_id": str(test_customer.id),
                }
            )

    # ----- update -----

    @pytest.mark.asyncio
    async def test_update_customer_name(self, tool, db, org_id, test_customer):
        """Update customer name."""
        result = await tool.run(
            {
                "operation": "update",
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
                "name": "Updated Name",
            }
        )

        data = json.loads(result)
        assert data["name"] == "Updated Name"

        # Verify in database
        await db.refresh(test_customer)
        assert test_customer.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_customer_email(self, tool, db, org_id, test_customer):
        """Update customer email."""
        result = await tool.run(
            {
                "operation": "update",
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
                "email": "updated@example.com",
            }
        )

        data = json.loads(result)
        assert data["email"] == "updated@example.com"

    @pytest.mark.asyncio
    async def test_update_customer_rejects_duplicate_email(self, tool, db, org_id, test_customer):
        """Reject updating to duplicate email."""
        # Create another customer
        other = Customer(
            id=uuid.uuid4(),
            organization_id=org_id,
            email="other@example.com",
            name="Other",
        )
        db.add(other)
        await db.commit()

        with pytest.raises(ValidationError, match="already exists"):
            await tool.run(
                {
                    "operation": "update",
                    "organization_id": str(org_id),
                    "customer_id": str(test_customer.id),
                    "email": "other@example.com",  # duplicate
                }
            )

    # ----- list -----

    @pytest.mark.asyncio
    async def test_list_customers(self, tool, db, org_id):
        """List customers with pagination."""
        # Create multiple customers
        for i in range(5):
            c = Customer(
                id=uuid.uuid4(),
                organization_id=org_id,
                email=f"customer{i}@example.com",
                name=f"Customer {i}",
            )
            db.add(c)
        await db.commit()

        result = await tool.run(
            {
                "operation": "list",
                "organization_id": str(org_id),
                "limit": 3,
                "offset": 0,
            }
        )

        data = json.loads(result)
        assert data["count"] == 3
        assert data["limit"] == 3
        assert data["offset"] == 0
        assert len(data["customers"]) == 3

        # Test pagination
        result2 = await tool.run(
            {
                "operation": "list",
                "organization_id": str(org_id),
                "limit": 3,
                "offset": 3,
            }
        )

        data2 = json.loads(result2)
        assert data2["count"] == 2  # remaining
        assert len(data2["customers"]) == 2

    @pytest.mark.asyncio
    async def test_list_customers_org_scoped(self, tool, db, org_id, other_org_id):
        """List only returns customers from the specified org."""
        # Create in both orgs
        c1 = Customer(id=uuid.uuid4(), organization_id=org_id, email="a@a.com", name="A")
        c2 = Customer(id=uuid.uuid4(), organization_id=other_org_id, email="b@b.com", name="B")
        db.add_all([c1, c2])
        await db.commit()

        result = await tool.run(
            {"operation": "list", "organization_id": str(org_id)}
        )
        data = json.loads(result)
        assert data["count"] == 1
        assert data["customers"][0]["email"] == "a@a.com"

    # ----- delete -----

    @pytest.mark.asyncio
    async def test_delete_customer(self, tool, test_session_factory, org_id, test_customer):
        """Delete a customer."""
        result = await tool.run(
            {
                "operation": "delete",
                "organization_id": str(org_id),
                "customer_id": str(test_customer.id),
            }
        )

        data = json.loads(result)
        assert data["deleted"] is True
        assert data["customer_id"] == str(test_customer.id)

        # Verify deleted using a fresh session
        async with test_session_factory() as session:
            customer = await session.get(Customer, test_customer.id)
            assert customer is None

    @pytest.mark.asyncio
    async def test_delete_customer_rejects_foreign_org(self, tool, db, org_id, other_org_id, test_customer):
        """Delete rejects customer from different org."""
        with pytest.raises(NotFoundError, match="Customer not found"):
            await tool.run(
                {
                    "operation": "delete",
                    "organization_id": str(other_org_id),
                    "customer_id": str(test_customer.id),
                }
            )

        # Verify not deleted
        customer = await db.get(Customer, test_customer.id)
        assert customer is not None


# ========================================================================
# Tool Registry integration
# ========================================================================


class TestSupportToolsRegistry:
    """Tests for tool registry integration."""

    @pytest.mark.asyncio
    async def test_all_tools_registered(self, test_session_factory):
        """All three tools are registered and have correct schemas."""
        from packages.core.tools import ToolRegistry

        tools = create_support_tools(session_factory=test_session_factory)
        registry = ToolRegistry(*tools)

        assert set(registry.names()) == {"send_email_reply", "create_ticket", "lookup_customer"}

        schemas = registry.list_schemas()
        assert len(schemas) == 3

        for schema in schemas:
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert schema["parameters"]["type"] == "object"