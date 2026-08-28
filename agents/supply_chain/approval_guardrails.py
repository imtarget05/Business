# -*- coding: utf-8 -*-
"""Guardrails cho Approval Workflow node.

Triển khai 3 tầng:
1. Input validation (po_data structure, route field, amount)
2. Permission check (chỉ transition approval state, không execute PO/touch DB)
3. Output verification (approval decision structure cố định)
"""

from __future__ import annotations

import logging
from typing import Any

from agents.supply_chain.approval import ApprovalWorkflow, ApprovalState
from agents.supply_chain.circuit_breaker import CircuitBreaker
from packages.contracts.models import TaskRequest

logger = logging.getLogger(__name__)


class ApprovalGuardrails:
    """Guardrails wrapper cho ApprovalWorkflow node trong LangGraph."""

    # PO amount threshold — anything above this must go through approval
    MIN_AMOUNT_FOR_APPROVAL = 0.0

    def __init__(self, workflow: ApprovalWorkflow | None = None) -> None:
        self._workflow = workflow
        # Circuit breaker protects the approval evaluation against repeated
        # downstream failures (LLM/approval-service flakiness).
        self._breaker = CircuitBreaker(f"approval_guardrails")

    def validate_input(self, request: TaskRequest) -> None:
        """Validate TaskRequest input cho Approval node.

        Raises:
            ValueError: Nếu input không hợp lệ.
        """
        payload = request.payload
        po_data = payload.get("po")

        # Rule 1: po_data phải là dict
        if po_data is not None and not isinstance(po_data, dict):
            raise ValueError(
                f"invalid po_data type: expected dict, got {type(po_data).__name__}"
            )

        # Rule 2: Nếu có po_data, route phải là string
        if isinstance(po_data, dict) and "route" in po_data:
            route = po_data.get("route")
            if not isinstance(route, str):
                raise ValueError(
                    f"invalid route type: expected str, got {type(route).__name__}"
                )

        # Rule 3: Nếu có total, phải là số >= 0
        if isinstance(po_data, dict) and "total" in po_data:
            total = po_data.get("total")
            if not isinstance(total, (int, float)):
                raise ValueError(
                    f"invalid total type: expected number, got {type(total).__name__}"
                )
            if total < 0:
                raise ValueError(f"total must be >= 0, got {total}")

        logger.debug(f"ApprovalGuardrails: input validation passed for task {request.task_id}")

    def check_permission(self, request: TaskRequest) -> None:
        """Kiểm tra permission scope cho Approval node.

        Approval workflow chỉ được:
        - Transition state (PENDING → APPROVED/REJECTED/EXPIRED)
        - Record decision
        Không được:
        - Execute PO (place order, 만들 purchase)
        - Modify PO data (sửa amount, vendor, items)
        - Touch DB trực tiếp
        """
        action = request.action

        # Danh sách action hợp lệ cho approval node
        allowed_actions = frozenset({
            "supply_chain_approve_po",
            "supply_chain_check_approval_status",
            "supply_chain_resolve_approval",
        })

        if action not in allowed_actions:
            raise PermissionError(
                f"unauthorized action for approval node: {action!r} "
                f"(allowed: {sorted(allowed_actions)})"
            )

        logger.debug(f"ApprovalGuardrails: permission check passed for task {request.task_id}")

    def validate_output(self, response: Any) -> None:
        """Validate output từ approval node.

        Raises:
            ValueError: Nếu output structure không hợp lệ.
        """
        # Output phải có status và result
        if not hasattr(response, "status") or not hasattr(response, "result"):
            raise ValueError(
                "approval output must have 'status' and 'result' attributes"
            )

        result = response.result
        if not isinstance(result, dict):
            raise ValueError(
                f"invalid approval result type: expected dict, got {type(result).__name__}"
            )

        # result phải có approval_status
        if "approval_status" not in result:
            raise ValueError("approval result must contain 'approval_status'")

        approval_status = result.get("approval_status")
        valid_statuses = {"auto_approved", "approved", "rejected", "expired", "pending"}
        if approval_status not in valid_statuses:
            raise ValueError(
                f"invalid approval_status: {approval_status!r} "
                f"(valid: {sorted(valid_statuses)})"
            )

        logger.debug(f"ApprovalGuardrails: output validation passed")
