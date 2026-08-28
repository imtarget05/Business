# -*- coding: utf-8 -*-
"""
Unit tests for supply_chain inbound handler (Phase SC).

Validates that process_inbound_email / process_inbound_batch correctly
route email content through the PurchaseOrderAgent.

Tests DO NOT require real Gmail credentials — they use mock email content
and the MockLLMProvider baked into the agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.supply_chain.inbound import (
    fetch_gmail_message_body,
    fetch_unread_gmail_messages,
    process_inbound_batch,
    process_inbound_email,
    process_queue_messages,
)
from agents.supply_chain.po_agent import PurchaseOrderAgent
from packages.config.settings import Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, TaskRequest, TaskContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def po_agent():
    """PurchaseOrderAgent with mock LLM for isolated inbound tests.

    The mock LLM returns None for generate_structured so the agent falls
    back to rule-based parsing (matching test_po_agent.py behavior).
    """
    from unittest.mock import AsyncMock, MagicMock

    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}
    llm = MagicMock()
    # Return None to force rule-based fallback (same as llm=None path)
    llm.generate_structured = AsyncMock(return_value=None)
    llm.generate = AsyncMock(return_value="new")
    return PurchaseOrderAgent(llm=llm, settings=settings)


# ---------------------------------------------------------------------------
# process_inbound_email — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_inbound_email_success(po_agent):
    """Valid email content is parsed, classified, and routed successfully."""
    email = (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Acme Corp\n"
        "Vendor Email: vendor@example.com\n"
        "Items:\n"
        "- SKU-001, Widget A - 10 units @ $5.00 each = $50.00 total\n"
        "TOTAL: $50.00\n"
    )

    resp = await process_inbound_email(email, po_agent=po_agent)

    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.agent == "purchase_order_agent-v1"
    assert resp.result["status"] == "processed"
    po = resp.result["po"]
    assert po["po_number"] == "PO-2024-001"
    assert po["vendor"] == "Acme Corp"
    assert po["route"] == "auto_approved"


@pytest.mark.asyncio
async def test_process_inbound_email_with_explicit_task_id(po_agent):
    """Pass an explicit task_id through to the response."""
    from uuid import uuid4

    task_id = uuid4()
    email = "PO NUMBER: PO-2024-TID\nVENDOR: Tid Test\nTOTAL: $10.00\n"

    resp = await process_inbound_email(
        email,
        task_id=task_id,
        po_agent=po_agent,
    )

    assert resp.task_id == task_id
    assert resp.status == AgentResponseStatus.SUCCESS


@pytest.mark.asyncio
async def test_process_inbound_email_auto_creates_task_id(po_agent):
    """When task_id is None, a fresh UUID v4 is generated."""
    email = "PO NUMBER: PO-2024-AUTO\nVENDOR: Auto Test\nTOTAL: $5.00\n"

    resp = await process_inbound_email(email, po_agent=po_agent)

    assert resp.task_id is not None
    assert resp.status == AgentResponseStatus.SUCCESS


@pytest.mark.asyncio
async def test_process_inbound_email_uses_provided_llm():
    """When po_agent is None, a fresh agent is created with the given LLM."""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.generate_structured = AsyncMock(
        return_value={
            "po_number": "PO-2024-LLM",
            "vendor": "LLM Vendor",
            "vendor_email": None,
            "date": None,
            "items": [],
            "total": 100.0,
        }
    )
    llm.generate = AsyncMock(return_value="new")

    email = "PO NUMBER: PO-2024-LLM\nVENDOR: LLM Vendor\nTOTAL: $100.00\n"

    resp = await process_inbound_email(email, llm=llm)

    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.result["po"]["po_number"] == "PO-2024-LLM"


# ---------------------------------------------------------------------------
# process_inbound_email — validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_inbound_email_rejects_empty_string():
    """Empty string should be rejected."""
    resp = await process_inbound_email("")

    assert resp.status == AgentResponseStatus.FAILED
    assert resp.error is not None
    assert "email_content must be a non-empty string" in resp.error.message


@pytest.mark.asyncio
async def test_process_inbound_email_rejects_non_string():
    """Non-string content should be rejected."""
    resp = await process_inbound_email(12345)  # type: ignore[arg-type]

    assert resp.status == AgentResponseStatus.FAILED
    assert resp.error is not None
    assert "email_content must be a non-empty string" in resp.error.message


@pytest.mark.asyncio
async def test_process_inbound_email_rejects_none():
    """None content should be rejected."""
    resp = await process_inbound_email(None)  # type: ignore[arg-type]

    assert resp.status == AgentResponseStatus.FAILED
    assert resp.error is not None


@pytest.mark.asyncio
async def test_process_inbound_email_with_context_fields(po_agent):
    """Task context fields (channel, org, user) are passed through."""
    from uuid import uuid4

    email = "PO NUMBER: PO-2024-CTX\nVENDOR: Context Vendor\nTOTAL: $10.00\n"

    resp = await process_inbound_email(
        email,
        channel="webhook",
        organization_id=uuid4(),
        user_id=uuid4(),
        trace_id="trace-789",
        po_agent=po_agent,
    )

    assert resp.status == AgentResponseStatus.SUCCESS


# ---------------------------------------------------------------------------
# process_inbound_batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_inbound_batch_empty_list(po_agent):
    """Empty batch returns empty result list."""
    results = await process_inbound_batch([], po_agent=po_agent)
    assert results == []


@pytest.mark.asyncio
async def test_process_inbound_batch_single_item(po_agent):
    """A batch with one item returns one response."""
    emails = [
        "PO NUMBER: PO-2024-BATCH-1\nVENDOR: Batch Vendor 1\nTOTAL: $10.00\n"
    ]
    results = await process_inbound_batch(emails, po_agent=po_agent)

    assert len(results) == 1
    assert results[0].status == AgentResponseStatus.SUCCESS
    assert results[0].metadata["batch_index"] == 0


@pytest.mark.asyncio
async def test_process_inbound_batch_multiple_items(po_agent):
    """Multiple emails are processed and results are in order."""
    emails = [
        "PO NUMBER: PO-2024-B1\nVENDOR: V1\nTOTAL: $10.00\n",
        "PO NUMBER: PO-2024-B2\nVENDOR: V2\nTOTAL: $20.00\n",
        "PO NUMBER: PO-2024-B3\nVENDOR: V3\nTOTAL: $30.00\n",
    ]
    results = await process_inbound_batch(emails, po_agent=po_agent)

    assert len(results) == 3
    for i, resp in enumerate(results):
        assert resp.status == AgentResponseStatus.SUCCESS
        assert resp.metadata["batch_index"] == i
        assert resp.result["po"]["po_number"] == f"PO-2024-B{i + 1}"


@pytest.mark.asyncio
async def test_process_inbound_batch_concurrency_limit(po_agent):
    """Batch processing respects concurrency semaphore."""
    # Create 10 emails with non-zero totals (TOTAL: $0.00 fails rule-based parsing)
    emails = [
        f"PO NUMBER: PO-2024-C{i}\nVENDOR: V{i}\nTOTAL: ${i + 1}.00\n"
        for i in range(10)
    ]
    results = await process_inbound_batch(
        emails,
        concurrency=3,
        po_agent=po_agent,
    )

    assert len(results) == 10
    for i, resp in enumerate(results):
        assert resp.status == AgentResponseStatus.SUCCESS
        assert resp.metadata["batch_index"] == i


@pytest.mark.asyncio
async def test_process_inbound_batch_mixed_valid_invalid(po_agent):
    """Batch with mix of valid and invalid emails returns appropriate responses."""
    emails = [
        "PO NUMBER: PO-2024-MIX-1\nVENDOR: Valid\nTOTAL: $10.00\n",
        "",  # invalid
        "PO NUMBER: PO-2024-MIX-3\nVENDOR: Also Valid\nTOTAL: $30.00\n",
    ]
    results = await process_inbound_batch(emails, po_agent=po_agent)

    assert len(results) == 3
    assert results[0].status == AgentResponseStatus.SUCCESS
    assert results[1].status == AgentResponseStatus.FAILED
    assert results[1].error is not None
    assert "email_content must be a non-empty string" in results[1].error.message
    assert results[2].status == AgentResponseStatus.SUCCESS


# ---------------------------------------------------------------------------
# process_queue_messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_queue_messages_valid(po_agent):
    """Queue messages with email_content are processed."""
    messages = [
        {
            "message_id": "msg-1",
            "email_content": "PO NUMBER: PO-2024-Q1\nVENDOR: Q Vendor\nTOTAL: $10.00\n",
        },
        {
            "message_id": "msg-2",
            "email_content": "PO NUMBER: PO-2024-Q2\nVENDOR: Q Vendor 2\nTOTAL: $20.00\n",
        },
    ]
    results = await process_queue_messages(messages)

    assert len(results) == 2
    assert results[0].status == AgentResponseStatus.SUCCESS
    assert results[0].metadata["queue_message_id"] == "msg-1"
    assert results[1].status == AgentResponseStatus.SUCCESS
    assert results[1].metadata["queue_message_id"] == "msg-2"


@pytest.mark.asyncio
async def test_process_queue_messages_missing_content():
    """Queue messages without email_content are rejected."""
    messages = [
        {"message_id": "msg-1", "other_field": "no content here"},
    ]
    results = await process_queue_messages(messages)

    assert len(results) == 1
    assert results[0].status == AgentResponseStatus.REJECTED
    assert results[0].error is not None
    assert "email_content" in results[0].error.message


@pytest.mark.asyncio
async def test_process_queue_messages_empty_list():
    """Empty message list returns empty result."""
    results = await process_queue_messages([])
    assert results == []


@pytest.mark.asyncio
async def test_process_queue_messages_custom_handler():
    """Custom handler function is used instead of default."""
    async def custom_handler(content: str):
        from packages.contracts.models import AgentResponse
        from packages.contracts.enums import AgentResponseStatus
        from uuid import uuid4

        return AgentResponse(
            task_id=uuid4(),
            agent="custom_handler",
            status=AgentResponseStatus.SUCCESS,
            result={"handled_by": "custom", "content_length": len(content)},
        )

    messages = [
        {"message_id": "msg-1", "email_content": "some content"},
    ]
    results = await process_queue_messages(messages, handler=custom_handler)

    assert len(results) == 1
    assert results[0].agent == "custom_handler"
    assert results[0].result["handled_by"] == "custom"


# ---------------------------------------------------------------------------
# Gmail API stubs — verify they raise NotImplementedError
# ---------------------------------------------------------------------------

def test_fetch_unread_gmail_messages_is_placeholder():
    """fetch_unread_gmail_messages is a stub that requires credentials."""
    with pytest.raises(NotImplementedError) as exc_info:
        # Can't call async function directly in sync test; use asyncio
        import asyncio
        asyncio.run(fetch_unread_gmail_messages())

    assert "placeholder" in str(exc_info.value).lower()
    assert "gmail" in str(exc_info.value).lower()


def test_fetch_gmail_message_body_is_placeholder():
    """fetch_gmail_message_body is a stub that requires credentials."""
    with pytest.raises(NotImplementedError) as exc_info:
        import asyncio
        asyncio.run(fetch_gmail_message_body("msg-123"))

    assert "placeholder" in str(exc_info.value).lower()
    assert "gmail" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Integration-style: process_inbound_email uses po_agent.handle()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_inbound_email_calls_po_agent_handle(po_agent):
    """Verify process_inbound_email delegates to po_agent.handle()."""
    email = "PO NUMBER: PO-2024-DELEGATE\nVENDOR: Delegate Test\nTOTAL: $5.00\n"

    # Patch handle to count calls
    original_handle = po_agent.handle
    call_count = 0

    async def counting_handle(request):
        nonlocal call_count
        call_count += 1
        return await original_handle(request)

    po_agent.handle = counting_handle

    resp = await process_inbound_email(email, po_agent=po_agent)

    assert call_count == 1
    assert resp.status == AgentResponseStatus.SUCCESS


@pytest.mark.asyncio
async def test_process_inbound_email_with_action_process_po(po_agent):
    """The action used is 'process_po' (supports full parse/classify/route)."""
    email = "PO NUMBER: PO-2024-ACTION\nVENDOR: Action Test\nTOTAL: $50.00\n"

    resp = await process_inbound_email(email, po_agent=po_agent)

    assert resp.status == AgentResponseStatus.SUCCESS
    # Full pipeline: parse + classify + route all succeeded
    assert resp.result["po"]["po_type"] in ("new", "reorder", "exchange")
    assert resp.result["po"]["route"] in (
        "auto_approved",
        "approval_required_manager_a",
        "approval_required_manager_b",
    )


# ---------------------------------------------------------------------------
# Data structure / metadata propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_inbound_email_metadata_forwarded(po_agent):
    """TaskRequest metadata is built with the provided trace_id."""
    email = "PO NUMBER: PO-2024-META\nVENDOR: Meta Test\nTOTAL: $5.00\n"

    resp = await process_inbound_email(
        email,
        po_agent=po_agent,
        trace_id="trace-abc-123",
    )

    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.result["po"]["po_number"] == "PO-2024-META"
