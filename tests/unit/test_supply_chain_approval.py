# -*- coding: utf-8 -*-
"""Unit tests for supply_chain approval workflow (Phase SC).

Validates the ApprovalWorkflow state machine, timeout handling,
and stub notification behavior.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from agents.supply_chain.approval import (
    ApprovalState,
    ApprovalWorkflow,
    create_approval_workflow,
    needs_approval,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def po_data_auto_approved() -> dict:
    """A PO that does not require human approval."""
    return {
        "po_number": "PO-2024-001",
        "vendor": "Acme Corp",
        "route": "auto_approved",
        "total": 100.0,
    }


@pytest.fixture
def po_data_needs_approval() -> dict:
    """A PO that requires human approval (manager_a)."""
    return {
        "po_number": "PO-2024-002",
        "vendor": "Big Vendor",
        "route": "approval_required_manager_a",
        "total": 1500.0,
    }


@pytest.fixture
def po_data_needs_approval_b() -> dict:
    """A PO that requires human approval (manager_b)."""
    return {
        "po_number": "PO-2024-003",
        "vendor": "Huge Vendor",
        "route": "approval_required_manager_b",
        "total": 15000.0,
    }


# ---------------------------------------------------------------------------
# Needs approval check
# ---------------------------------------------------------------------------

def test_needs_approval_auto_approved(po_data_auto_approved):
    """auto_approved route does not need human approval."""
    assert needs_approval(po_data_auto_approved) is False


def test_needs_approval_manager_a(po_data_needs_approval):
    """approval_required_manager_a needs human approval."""
    assert needs_approval(po_data_needs_approval) is True


def test_needs_approval_manager_b(po_data_needs_approval_b):
    """approval_required_manager_b needs human approval."""
    assert needs_approval(po_data_needs_approval_b) is True


def test_needs_approval_missing_route():
    """Missing route defaults to auto_approved."""
    assert needs_approval({"po_number": "PO-001"}) is False


# ---------------------------------------------------------------------------
# ApprovalWorkflow initialization
# ---------------------------------------------------------------------------

def test_approval_workflow_initial_state(po_data_needs_approval):
    """Workflow starts in PENDING state."""
    workflow = ApprovalWorkflow(po_data=po_data_needs_approval)
    assert workflow.state == ApprovalState.PENDING


def test_approval_workflow_po_data(po_data_needs_approval):
    """Workflow stores the PO data."""
    workflow = ApprovalWorkflow(po_data=po_data_needs_approval)
    assert workflow.po["po_number"] == "PO-2024-002"
    assert workflow.po["route"] == "approval_required_manager_a"


def test_approval_workflow_approver_email(po_data_needs_approval):
    """Workflow stores the approver email."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    assert workflow._context.approver_email == "manager@example.com"


def test_approval_workflow_timeout_configurable(po_data_needs_approval):
    """Timeout is configurable."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        timeout_seconds=3600.0,  # 1 hour
    )
    assert workflow._context.timeout_seconds == 3600.0


def test_approval_workflow_default_timeout(po_data_needs_approval):
    """Default timeout is 24 hours."""
    workflow = ApprovalWorkflow(po_data=po_data_needs_approval)
    assert workflow._context.timeout_seconds == 86400.0


# ---------------------------------------------------------------------------
# handle() — auto-approved POs skip workflow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_auto_approved_skips_workflow(po_data_auto_approved):
    """Auto-approved POs return success without human approval."""
    workflow = ApprovalWorkflow(po_data=po_data_auto_approved)
    request = MagicMock()
    request.task_id = uuid4()
    request.payload = {"po": po_data_auto_approved}

    response = await workflow.handle(request)

    assert response.status == "success"
    assert response.result["approval_status"] == "auto_approved"
    assert workflow.state == ApprovalState.PENDING  # State unchanged


# ---------------------------------------------------------------------------
# handle() — POs needing approval transition to pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_approval_pending(po_data_needs_approval):
    """PO needing approval transitions to PENDING_HUMAN_APPROVAL."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    request = MagicMock()
    request.task_id = uuid4()
    request.payload = {"po": po_data_needs_approval}

    # Stub the notify function to avoid NotImplementedError
    with patch.object(
        ApprovalWorkflow, "_notify_human_approver", new_callable=AsyncMock
    ) as mock_notify:
        response = await workflow.handle(request)

    assert response.status == "escalated"
    assert response.error is not None
    assert response.error.code == "APPROVAL_PENDING"
    assert workflow.state == ApprovalState.PENDING_HUMAN_APPROVAL
    assert mock_notify.called


# ---------------------------------------------------------------------------
# handle() — notification stub raises NotImplementedError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_notification_stub_not_implemented(po_data_needs_approval):
    """Notification stub raises NotImplementedError when called."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )

    # Directly test the stub
    with pytest.raises(NotImplementedError) as exc_info:
        await workflow._notify_human_approver(po_data_needs_approval)

    assert "placeholder" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# resolve() — human decision processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_approved(po_data_needs_approval):
    """Human approves the PO."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL

    response = await workflow.resolve(decision="approved", decided_by="manager_john")

    assert response.status == "success"
    assert response.result["approval_status"] == "approved"
    assert response.result["decision"] == "approved"
    assert response.result["decided_by"] == "manager_john"
    assert workflow.state == ApprovalState.APPROVED


@pytest.mark.asyncio
async def test_resolve_rejected(po_data_needs_approval):
    """Human rejects the PO."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL

    response = await workflow.resolve(decision="rejected", decided_by="manager_jane")

    assert response.status == "failed"
    assert response.result["approval_status"] == "rejected"
    assert response.result["decision"] == "rejected"
    assert response.result["decided_by"] == "manager_jane"
    assert workflow.state == ApprovalState.REJECTED


# ---------------------------------------------------------------------------
# resolve() — invalid states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_invalid_state(po_data_needs_approval):
    """Cannot resolve when not in PENDING_HUMAN_APPROVAL state."""
    workflow = ApprovalWorkflow(po_data=po_data_needs_approval)
    # State is PENDING, not PENDING_HUMAN_APPROVAL

    response = await workflow.resolve(decision="approved")

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "INVALID_STATE"
    assert "pending" in response.error.message


@pytest.mark.asyncio
async def test_resolve_already_resolved(po_data_needs_approval):
    """Cannot resolve after already resolved."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.APPROVED

    response = await workflow.resolve(decision="approved")

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "INVALID_STATE"


# ---------------------------------------------------------------------------
# resolve() — missing decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_missing_decision(po_data_needs_approval):
    """Resolve without decision returns error."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL

    response = await workflow.resolve()

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "MISSING_DECISION"


# ---------------------------------------------------------------------------
# resolve() — invalid decision value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_invalid_decision(po_data_needs_approval):
    """Invalid decision value returns error."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL

    response = await workflow.resolve(decision="maybe", decided_by="manager_test")

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "INVALID_DECISION"
    assert "approved" in response.error.message or "rejected" in response.error.message


# ---------------------------------------------------------------------------
# resolve() — timeout handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_timeout(po_data_needs_approval):
    """Timeout triggers EXPIRED state."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        timeout_seconds=0.001,  # Very short timeout
    )
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL
    # Force elapsed > timeout deterministically (no sleep race)
    import time as _t

    workflow._context.requested_at = _t.monotonic() - 10.0

    response = await workflow.resolve(decision="approved", decided_by="manager_test")

    assert response.status == "failed"
    assert response.error is not None
    assert response.error.code == "APPROVAL_TIMEOUT"
    assert workflow.state == ApprovalState.EXPIRED


# ---------------------------------------------------------------------------
# get_status() — status monitoring
# ---------------------------------------------------------------------------

def test_get_status_pending(po_data_needs_approval):
    """get_status returns current state info."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )

    status = workflow.get_status()

    assert status["state"] == "pending"
    assert status["po_number"] == "PO-2024-002"
    assert status["approver_email"] == "manager@example.com"
    assert status["timeout_seconds"] == 86400.0
    assert "elapsed_seconds" in status


def test_get_status_after_approval(po_data_needs_approval):
    """get_status reflects resolved state."""
    workflow = ApprovalWorkflow(
        po_data=po_data_needs_approval,
        approver_email="manager@example.com",
    )
    workflow._context.state = ApprovalState.APPROVED
    workflow._context.resolved_at = time.time()

    status = workflow.get_status()

    assert status["state"] == "approved"
    assert status["resolved_at"] is not None
    assert status["approver_email"] == "manager@example.com"


# ---------------------------------------------------------------------------
# create_approval_workflow convenience function
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_approval_workflow():
    """create_approval_workflow returns an ApprovalWorkflow instance."""
    po_data = {"po_number": "PO-2024-001", "route": "auto_approved"}

    workflow = create_approval_workflow(
        po_data=po_data,
        approver_email="manager@example.com",
        timeout_seconds=3600.0,
    )

    assert isinstance(workflow, ApprovalWorkflow)
    assert workflow.po["po_number"] == "PO-2024-001"


# ---------------------------------------------------------------------------
# Integration-style: workflow with PO Agent output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_with_po_agent_output():
    """Approval workflow works with PO Agent output format."""
    # Simulate PO Agent output
    po_agent_output = {
        "po_number": "PO-2024-100",
        "vendor": "Test Vendor",
        "vendor_email": "vendor@example.com",
        "items": [
            {
                "sku": "SKU-001",
                "description": "Widget",
                "quantity": 10,
                "unit_price": 5.0,
                "total_price": 50.0,
            }
        ],
        "total": 50.0,
        "po_type": "new",
        "route": "approval_required_manager_a",
    }

    workflow = ApprovalWorkflow(
        po_data=po_agent_output,
        approver_email="manager@example.com",
    )

    # Verify PO data is correctly stored
    assert workflow.po["po_number"] == "PO-2024-100"
    assert workflow.po["vendor"] == "Test Vendor"
    assert len(workflow.po["items"]) == 1
    assert workflow.po["route"] == "approval_required_manager_a"


@pytest.mark.asyncio
async def test_workflow_auto_approved_route_skips(po_data_auto_approved):
    """Workflow with auto_approved route returns early."""
    workflow = ApprovalWorkflow(po_data=po_data_auto_approved)
    request = MagicMock()
    request.task_id = uuid4()
    request.payload = {"po": po_data_auto_approved}

    response = await workflow.handle(request)

    # Should return success immediately without waiting for approval
    assert response.status == "success"
    assert response.result["approval_status"] == "auto_approved"
    # State should remain PENDING (never transitioned to PENDING_HUMAN_APPROVAL)
    assert workflow.state == ApprovalState.PENDING
