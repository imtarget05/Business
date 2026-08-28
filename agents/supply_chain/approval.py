# -*- coding: utf-8 -*-
"""Approval Workflow for Purchase Orders (Phase SC).

Implements a human-in-the-loop approval process for POs that require
manager approval based on policy thresholds.

State machine: PENDING → PENDING_HUMAN_APPROVAL → {APPROVED, REJECTED, EXPIRED}

Notification is a STUB — real notification channels (email, Slack, etc.)
must be implemented by the user when credentials are available.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from packages.contracts.enums import AgentResponseStatus
from packages.contracts.models import AgentResponse, ErrorDetail, TaskRequest

logger = logging.getLogger(__name__)


class ApprovalState(StrEnum):
    """States for the PO approval workflow."""

    PENDING = "pending"
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def _now_seconds() -> float:
    """Get current time in seconds — works inside and outside async context."""
    try:
        import asyncio
        return asyncio.get_event_loop().time()
    except RuntimeError:
        return time.monotonic()


@dataclass
class ApprovalContext:
    """Holds the state and metadata for a PO approval workflow."""

    po: dict[str, Any]
    state: ApprovalState = ApprovalState.PENDING
    approver_email: str | None = None
    decided_by: str | None = None
    decision: str | None = None  # "approved" | "rejected"
    requested_at: float = field(default_factory=_now_seconds)
    resolved_at: float | None = None
    timeout_seconds: float = 86400.0  # 24 hours default


class ApprovalWorkflow:
    """Human-in-the-loop approval workflow for purchase orders.

    Usage:
        workflow = ApprovalWorkflow(po_data, approver_email="manager@example.com")
        result = await workflow.handle(request)

    The workflow:
    1. Checks if PO requires approval (based on route field)
    2. If yes, transitions to PENDING_HUMAN_APPROVAL and notifies human (stub)
    3. Waits for human decision (via resolve() method)
    4. Returns APPROVED or REJECTED based on decision
    5. If timeout expires, returns EXPIRED
    """

    def __init__(
        self,
        po_data: dict[str, Any],
        *,
        approver_email: str | None = None,
        timeout_seconds: float = 86400.0,
    ) -> None:
        """Initialize the approval workflow.

        Args:
            po_data: The purchase order data dict (from PO Agent output).
            approver_email: Email of the human approver (optional).
            timeout_seconds: Maximum time to wait for human decision (default 24h).
        """
        self._context = ApprovalContext(
            po=po_data,
            approver_email=approver_email,
            timeout_seconds=timeout_seconds,
        )

    @property
    def state(self) -> ApprovalState:
        """Current approval state."""
        return self._context.state

    @property
    def po(self) -> dict[str, Any]:
        """The purchase order data."""
        return self._context.po

    async def handle(self, request: TaskRequest) -> AgentResponse:
        """Process the approval workflow for a PO.

        This is the main entry point called by the orchestrator.
        It checks if the PO needs approval and transitions state accordingly.

        Args:
            request: The task request containing PO data.

        Returns:
            AgentResponse with approval decision or status.
        """
        po = request.payload.get("po", {})
        route = po.get("route", "auto_approved")

        # Check if approval is needed
        needs_approval = route in (
            "approval_required_manager_a",
            "approval_required_manager_b",
        )

        if not needs_approval:
            # Auto-approved POs skip the human approval workflow
            return AgentResponse(
                task_id=request.task_id,
                agent="approval_workflow-v1",
                status=AgentResponseStatus.SUCCESS,
                result={
                    "po": po,
                    "approval_status": "auto_approved",
                    "message": "PO does not require human approval",
                },
            )

        # Transition to pending human approval
        self._context.state = ApprovalState.PENDING_HUMAN_APPROVAL

        # Stub: notify human approver (not implemented)
        await self._notify_human_approver(po)

        return AgentResponse(
            task_id=request.task_id,
            agent="approval_workflow-v1",
            status=AgentResponseStatus.ESCALATED,
            error=ErrorDetail(
                code="APPROVAL_PENDING",
                message="Awaiting human approval decision",
            ),
            metadata={
                "approval_context": {
                    "state": self._context.state.value,
                    "approver_email": self._context.approver_email,
                    "timeout_seconds": self._context.timeout_seconds,
                },
            },
        )

    async def _notify_human_approver(self, po: dict[str, Any]) -> None:
        """Stub: notify the human approver.

        REAL IMPLEMENTATION REQUIRES:
          - Notification channel (email, Slack, Teams, etc.)
          - Credentials for the notification service
          - Configuration for which channel to use

        This stub raises NotImplementedError to indicate that real
        notification is not implemented.
        """
        raise NotImplementedError(
            "notify_human_approver is a placeholder. "
            "Real notification channel implementation is required. "
            "See module docstring for the credential checklist."
        )

    async def resolve(
        self,
        *,
        decision: str | None = None,
        decided_by: str | None = None,
    ) -> AgentResponse:
        """Resolve the approval workflow with a human decision.

        This method should be called when the human approver makes a decision.
        It can be invoked via a webhook, API endpoint, or manual trigger.

        Args:
            decision: The human's decision — "approved" or "rejected".
            decided_by: Identifier of the person making the decision.

        Returns:
            AgentResponse with the final approval decision.
        """
        if self._context.state != ApprovalState.PENDING_HUMAN_APPROVAL:
            return AgentResponse(
                task_id=UUID("00000000-0000-0000-0000-000000000000"),
                agent="approval_workflow-v1",
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="INVALID_STATE",
                    message=f"Cannot resolve approval in state {self._context.state.value}",
                ),
            )

        # Check for timeout
        current_time = _now_seconds()
        elapsed = current_time - self._context.requested_at

        if elapsed > self._context.timeout_seconds:
            self._context.state = ApprovalState.EXPIRED
            self._context.resolved_at = current_time
            return AgentResponse(
                task_id=UUID("00000000-0000-0000-0000-000000000000"),
                agent="approval_workflow-v1",
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="APPROVAL_TIMEOUT",
                    message=f"Approval timed out after {self._context.timeout_seconds}s",
                ),
                result={
                    "po": self._context.po,
                    "approval_status": "expired",
                    "message": "Human approval not received within timeout period",
                },
            )

        # Process the human decision
        if decision is None:
            return AgentResponse(
                task_id=UUID("00000000-0000-0000-0000-000000000000"),
                agent="approval_workflow-v1",
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="MISSING_DECISION",
                    message="Human decision is required (approved or rejected)",
                ),
            )

        decision_lower = decision.lower()
        if decision_lower not in ("approved", "rejected"):
            return AgentResponse(
                task_id=UUID("00000000-0000-0000-0000-000000000000"),
                agent="approval_workflow-v1",
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="INVALID_DECISION",
                    message=f"Invalid decision: {decision!r}. Must be 'approved' or 'rejected'.",
                ),
            )

        self._context.state = ApprovalState.APPROVED if decision_lower == "approved" else ApprovalState.REJECTED
        self._context.decision = decision_lower
        self._context.decided_by = decided_by
        self._context.resolved_at = _now_seconds()

        status = AgentResponseStatus.SUCCESS if decision_lower == "approved" else AgentResponseStatus.FAILED

        error = ErrorDetail(
            code="APPROVAL_REJECTED",
            message=f"PO rejected by {decided_by or 'unknown'}",
        ) if decision_lower == "rejected" else None

        return AgentResponse(
            task_id=UUID("00000000-0000-0000-0000-000000000000"),
            agent="approval_workflow-v1",
            status=status,
            error=error,
            result={
                "po": self._context.po,
                "approval_status": self._context.state.value,
                "decision": self._context.decision,
                "decided_by": self._context.decided_by,
                "resolved_at": self._context.resolved_at,
                "message": f"PO {self._context.state.value} by {decided_by or 'unknown'}",
            },
        )

    def get_status(self) -> dict[str, Any]:
        """Get the current approval status for monitoring/UI.

        Returns:
            Dict with current state and metadata.
        """
        return {
            "state": self._context.state.value,
            "po_number": self._context.po.get("po_number"),
            "approver_email": self._context.approver_email,
            "requested_at": self._context.requested_at,
            "resolved_at": self._context.resolved_at,
            "timeout_seconds": self._context.timeout_seconds,
            "elapsed_seconds": _now_seconds() - self._context.requested_at,
        }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def needs_approval(po_data: dict[str, Any]) -> bool:
    """Check if a PO requires human approval based on its route.

    Args:
        po_data: The purchase order data dict.

    Returns:
        True if the PO requires human approval, False otherwise.
    """
    route = po_data.get("route", "auto_approved")
    return route in (
        "approval_required_manager_a",
        "approval_required_manager_b",
    )


def create_approval_workflow(
    po_data: dict[str, Any],
    *,
    approver_email: str | None = None,
    timeout_seconds: float = 86400.0,
) -> ApprovalWorkflow:
    """Create an ApprovalWorkflow instance for a PO.

    Args:
        po_data: The purchase order data dict.
        approver_email: Email of the human approver.
        timeout_seconds: Maximum wait time for approval.

    Returns:
        ApprovalWorkflow instance.
    """
    return ApprovalWorkflow(
        po_data=po_data,
        approver_email=approver_email,
        timeout_seconds=timeout_seconds,
    )
