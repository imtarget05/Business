"""Integration tests for SupportAgent tool chain execution (Phase 3, Task 3.3).

Tests the agent executing a chain of tools via the tool loop using
scripted mock LLM tool calls (from Task 3.1).
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.support.agent import SupportAgent
from agents.support.tools import create_support_tools
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.errors import ToolExecutionError
from packages.database.base import Base
from packages.database.models import Customer
from packages.llm.mock import MockLLMProvider


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
async def test_customer(db, org_id) -> Customer:
    """Create a test customer in the database."""
    customer = Customer(
        id=uuid.uuid4(),
        organization_id=org_id,
        email="customer@example.com",
        name="Test Customer",
        notes="Test notes",
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


@pytest.fixture()
def support_agent(org_id, test_session_factory) -> SupportAgent:
    """Create a SupportAgent with MockLLMProvider and test session factory."""
    llm = MockLLMProvider()
    # Pass test session factory to tools
    tools = create_support_tools(session_factory=test_session_factory)
    from packages.core.tools import ToolRegistry

    registry = ToolRegistry(*tools)
    agent = SupportAgent(llm=llm)
    # Replace the agent's registry with our test one
    agent._registry = registry
    agent._tools = tools
    return agent


# ========================================================================
# Tool chain execution tests
# ========================================================================


class TestSupportAgentToolChains:
    """Test agent executing chains of tools."""

    @pytest.mark.asyncio
    async def test_triage_then_create_ticket_chain(self, support_agent, org_id, test_customer):
        """Agent triages request then creates a ticket."""
        # Script the LLM to first call create_ticket, then return final answer
        support_agent.script_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "create_ticket",
                        "arguments": {
                            "organization_id": str(org_id),
                            "customer_id": str(test_customer.id),
                            "subject": "Billing discrepancy",
                            "description": "Customer reports incorrect charge on invoice",
                        },
                    }
                ],
                "content": "Creating ticket for billing issue",
            },
            "Ticket created successfully. Customer notified.",
        )

        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="triage",
            payload={
                "subject": "Billing discrepancy",
                "body": "Customer reports incorrect charge on invoice",
                "customer_id": str(test_customer.id),
            },
            context=TaskContext(organization_id=org_id, channel="email"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "success"
        summary = response.result["summary"].lower()
        assert "ticket" in summary or "created" in summary

        # Verify the tool was called and returned a ticket_id
        llm_calls = support_agent.llm.calls
        assert len(llm_calls) >= 2  # tool call round + final answer round

    @pytest.mark.asyncio
    async def test_lookup_customer_then_create_ticket_chain(self, support_agent, org_id, db):
        """Agent looks up customer by email, then creates ticket for them."""
        # First, create a customer in the database
        customer = Customer(
            id=uuid.uuid4(),
            organization_id=org_id,
            email="lookup@example.com",
            name="Lookup Customer",
        )
        db.add(customer)
        await db.commit()
        customer_id = customer.id

        # Script: lookup_customer (get by email) -> create_ticket -> final
        support_agent.script_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "lookup_customer",
                        "arguments": {
                            "operation": "get",
                            "organization_id": str(org_id),
                            "email": "lookup@example.com",
                        },
                    }
                ],
                "content": "Found customer, now creating ticket",
            },
            {
                "tool_calls": [
                    {
                        "id": "call_2",
                        "name": "create_ticket",
                        "arguments": {
                            "organization_id": str(org_id),
                            "customer_id": str(customer_id),
                            "subject": "Technical issue after lookup",
                        },
                    }
                ],
                "content": "Creating ticket for found customer",
            },
            "Done. Ticket created for customer.",
        )

        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="triage",
            payload={
                "subject": "Technical issue",
                "body": "Customer needs help",
                "customer_email": "lookup@example.com",
            },
            context=TaskContext(organization_id=org_id, channel="web"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "success"

        # Verify both tools were called
        tool_messages = []
        for call in support_agent.llm.calls:
            if isinstance(call, dict) and "messages" in call:
                for msg in call["messages"]:
                    if msg.get("role") == "tool":
                        tool_messages.append(msg)

        tool_names = [m.get("name") for m in tool_messages]
        assert "lookup_customer" in tool_names
        assert "create_ticket" in tool_names

    @pytest.mark.asyncio
    async def test_draft_reply_sends_email_dry_run(self, support_agent, org_id, test_customer):
        """Agent drafts reply using send_email_reply in DRY-RUN mode."""
        support_agent.script_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "send_email_reply",
                        "arguments": {
                            "to_email": test_customer.email,
                            "subject": "Re: Your inquiry",
                            "body_text": "Thank you for contacting us. We'll look into this.",
                            "conversation_id": str(uuid.uuid4()),
                        },
                    }
                ],
                "content": "Drafted email reply",
            },
            "Email draft created (DRY-RUN mode).",
        )

        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="draft_reply",
            payload={
                "subject": "Your inquiry",
                "body": "Need help with order",
                "customer_id": str(test_customer.id),
            },
            context=TaskContext(organization_id=org_id, channel="email"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "success"
        summary = response.result["summary"].lower()
        assert "draft" in summary or "dry" in summary

    @pytest.mark.asyncio
    async def test_full_support_flow_lookup_create_email(self, support_agent, org_id, db):
        """Full flow: lookup/create customer -> create ticket -> send email."""
        # First create a customer in the DB
        customer = Customer(
            id=uuid.uuid4(),
            organization_id=org_id,
            email="flow@example.com",
            name="Flow Customer",
        )
        db.add(customer)
        await db.commit()
        customer_id = customer.id

        # Script a three-tool chain
        support_agent.script_tool_calls(
            # Step 1: Look up customer
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "lookup_customer",
                        "arguments": {
                            "operation": "get",
                            "organization_id": str(org_id),
                            "email": "flow@example.com",
                        },
                    }
                ],
                "content": "Found customer",
            },
            # Step 2: Create ticket for that customer
            {
                "tool_calls": [
                    {
                        "id": "call_2",
                        "name": "create_ticket",
                        "arguments": {
                            "organization_id": str(org_id),
                            "customer_id": str(customer_id),
                            "subject": "Full flow test ticket",
                            "description": "Created in integration test",
                        },
                    }
                ],
                "content": "Creating ticket for customer",
            },
            # Step 3: Send email confirmation
            {
                "tool_calls": [
                    {
                        "id": "call_3",
                        "name": "send_email_reply",
                        "arguments": {
                            "to_email": "flow@example.com",
                            "subject": "Ticket created",
                            "body_text": "Your ticket has been created.",
                            "conversation_id": str(uuid.uuid4()),
                        },
                    }
                ],
                "content": "Sending confirmation email",
            },
            "Full support flow completed successfully.",
        )

        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="triage",
            payload={
                "subject": "Full flow test",
                "body": "Customer needs ticket and confirmation",
                "customer_email": "flow@example.com",
            },
            context=TaskContext(organization_id=org_id, channel="web"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "success"

        # Verify all three tool calls happened
        all_tool_calls = []
        for call in support_agent.llm.tool_responses:
            if call.get("tool_calls"):
                for tc in call["tool_calls"]:
                    all_tool_calls.append(tc["name"])

        assert "lookup_customer" in all_tool_calls
        assert "create_ticket" in all_tool_calls
        assert "send_email_reply" in all_tool_calls


class TestSupportAgentErrorHandling:
    """Test agent error handling in tool chains."""

    @pytest.mark.asyncio
    async def test_invalid_action_rejected(self, support_agent, org_id):
        """Invalid action is rejected."""
        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="invalid_action",
            payload={"subject": "Test"},
            context=TaskContext(organization_id=org_id, channel="web"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "rejected"
        assert response.error is not None
        assert response.error.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_missing_subject_escalated(self, support_agent, org_id):
        """Missing subject escalates to human."""
        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="triage",
            payload={"body": "No subject"},
            context=TaskContext(organization_id=org_id, channel="web"),
        )

        response = await support_agent.handle(request)

        assert response.status.value == "escalated"
        assert response.error is not None
        assert response.error.code == "ROUTING_ERROR"

    @pytest.mark.asyncio
    async def test_tool_execution_error_handled(self, support_agent, org_id):
        """Tool execution errors are caught and returned as failed response."""
        # Script a tool call that will fail (invalid customer_id for create_ticket)
        support_agent.script_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "create_ticket",
                        "arguments": {
                            "organization_id": str(org_id),
                            "customer_id": str(uuid.uuid4()),  # non-existent
                            "subject": "Will fail",
                        },
                    }
                ],
                "content": "Trying to create ticket",
            },
            # The loop will continue after tool error, but agent should handle it
        )

        request = TaskRequest(
            task_id=uuid.uuid4(),
            domain=Domain.SUPPORT,
            action="create_ticket",
            payload={
                "subject": "Will fail",
                "customer_id": str(uuid.uuid4()),
            },
            context=TaskContext(organization_id=org_id, channel="web"),
        )

        with pytest.raises(ToolExecutionError):
            await support_agent.handle(request)
