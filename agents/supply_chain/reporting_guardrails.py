# -*- coding: utf-8 -*-
"""Guardrails cho Reporting Agent node.

Triển khai 3 tầng:
1. Input validation (action, payload structure, org scoping)
2. Permission check (chỉ generate reports, không modify data)
3. Output verification (report structure cố định, không raw data dump)
"""

from __future__ import annotations

import logging
from typing import Any

from agents.supply_chain.circuit_breaker import CircuitBreaker
from packages.contracts.models import TaskRequest

logger = logging.getLogger(__name__)


class ReportingGuardrails:
    """Guardrails wrapper cho SupplyChainReporter node trong LangGraph."""

    # Valid report types
    VALID_REPORT_TYPES = frozenset({
        "daily_summary",
        "po_processing",
        "approval_stats",
        "inventory_alerts",
        "full_dashboard",
    })

    # Valid actions for reporting node
    VALID_ACTIONS = frozenset({
        "supply_chain_generate_report",
        "supply_chain_get_dashboard",
        "supply_chain_get_po_report",
        "supply_chain_get_approval_report",
        "supply_chain_get_inventory_report",
    })

    def __init__(self) -> None:
        # Circuit breaker guards the report generation (DB-backed aggregation).
        self._breaker = CircuitBreaker("reporting_guardrails")

    def validate_input(self, request: TaskRequest) -> None:
        """Validate TaskRequest input cho Reporting node.

        Raises:
            ValueError: Nếu input không hợp lệ.
            PermissionError: Nếu action không được phép.
        """
        payload = request.payload
        action = request.action

        # Rule 1: action phải là valid reporting action
        if action not in self.VALID_ACTIONS:
            raise PermissionError(
                f"unauthorized action for reporting node: {action!r} "
                f"(allowed: {sorted(self.VALID_ACTIONS)})"
            )

        # Rule 2: Nếu có report_type, phải là valid type
        report_type = payload.get("report_type")
        if report_type is not None:
            if not isinstance(report_type, str):
                raise ValueError(
                    f"invalid report_type type: expected str, got {type(report_type).__name__}"
                )
            if report_type not in self.VALID_REPORT_TYPES:
                raise ValueError(
                    f"invalid report_type: {report_type!r} "
                    f"(valid: {sorted(self.VALID_REPORT_TYPES)})"
                )

        # Rule 3: Nếu có organization_id, phải là string
        org_id = payload.get("organization_id")
        if org_id is not None and not isinstance(org_id, str):
            raise ValueError(
                f"invalid organization_id type: expected str, got {type(org_id).__name__}"
            )

        logger.debug(f"ReportingGuardrails: input validation passed for task {request.task_id}")

    def check_permission(self, request: TaskRequest) -> None:
        """Kiểm tra permission scope — reporting là read-only aggregation.

        Reporting Agent chỉ được:
        - Read aggregate data (POs, approvals, inventory)
        - Generate reports/dashboards
        - Compute metrics

        Không được:
        - Modify PO data
        - Modify approval decisions
        - Modify inventory data
        - Execute transactions
        - Access raw data beyond aggregation scope
        """
        action = request.action

        if action not in self.VALID_ACTIONS:
            raise PermissionError(
                f"unauthorized action for reporting node: {action!r}"
            )

        # Reporting node là read-only — không có write actions
        write_actions = frozenset({
            "supply_chain.create_po",
            "supply_chain.approve_po",
            "supply_chain.reject_po",
            "supply_chain.update_inventory",
            "supply_chain.adjust_stock",
            "supply_chain.place_order",
            "supply_chain.delete_record",
        })

        if action in write_actions:
            raise PermissionError(
                f"reporting node cannot perform write action: {action!r}"
            )

        logger.debug(f"ReportingGuardrails: permission check passed for task {request.task_id}")

    def validate_output(self, response: Any) -> None:
        """Validate output từ reporting node.

        Raises:
            ValueError: Nếu output structure không hợp lệ.
            PermissionError: Nếu output chứa sensitive data không nên expose.
        """
        # Response phải có status và result
        if not hasattr(response, "status") or not hasattr(response, "result"):
            raise ValueError(
                "reporting output must have 'status' and 'result' attributes"
            )

        result = response.result
        if not isinstance(result, dict):
            raise ValueError(
                f"invalid reporting result type: expected dict, got {type(result).__name__}"
            )

        # report_type phải có
        report_type = result.get("report_type")
        if report_type is not None and not isinstance(report_type, str):
            raise ValueError(
                f"invalid report_type in result: expected str, got {type(report_type).__name__}"
            )

        # Nếu có dashboard, phải là dict
        dashboard = result.get("dashboard")
        if dashboard is not None and not isinstance(dashboard, dict):
            raise ValueError(
                f"invalid dashboard type: expected dict, got {type(dashboard).__name__}"
            )

        # Kiểm tra không có sensitive fields trong output
        sensitive_fields = {"api_keys", "secrets", "passwords", "credentials"}
        for key in result.keys():
            if key.lower() in sensitive_fields:
                raise PermissionError(
                    f"reporting output contains sensitive field: {key!r}"
                )

        logger.debug(f"ReportingGuardrails: output validation passed")
