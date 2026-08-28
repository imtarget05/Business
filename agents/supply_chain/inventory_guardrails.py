# -*- coding: utf-8 -*-
"""Guardrails cho Inventory Monitor node.

Triển khai 3 tầng:
1. Input validation (items structure, SKU format, quantity range)
2. Permission check (chỉ read-only monitoring, không modify inventory data)
3. Output verification (alerts/summary structure cố định)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from packages.contracts.models import TaskRequest

logger = logging.getLogger(__name__)


class InventoryGuardrails:
    """Guardrails wrapper cho Inventory Monitor node trong LangGraph."""

    # SKU pattern: alphanumeric with hyphens/underscores, 3-30 chars
    SKU_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,29}$")

    # Valid actions for inventory node
    VALID_ACTIONS = frozenset({
        "supply_chain_check_inventory",
        "supply_chain_get_alerts",
        "supply_chain_get_summary",
    })

    def __init__(self) -> None:
        pass

    def validate_input(self, request: TaskRequest) -> None:
        """Validate TaskRequest input cho Inventory node.

        Raises:
            ValueError: Nếu input không hợp lệ.
            PermissionError: Nếu action không được phép.
        """
        payload = request.payload
        action = request.action

        # Rule 1: action phải là valid inventory action
        if action not in self.VALID_ACTIONS:
            raise PermissionError(
                f"unauthorized action for inventory node: {action!r} "
                f"(allowed: {sorted(self.VALID_ACTIONS)})"
            )

        # Rule 2: items phải là list (nếu có)
        items = payload.get("items")
        if items is not None:
            if not isinstance(items, list):
                raise ValueError(
                    f"invalid items type: expected list, got {type(items).__name__}"
                )

            # Rule 3: Mỗi item phải có SKU hợp lệ
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"invalid item[{i}] type: expected dict, got {type(item).__name__}"
                    )

                sku = item.get("sku", "")
                if not isinstance(sku, str) or not sku.strip():
                    raise ValueError(
                        f"item[{i}] missing or invalid sku: {sku!r}"
                    )
                if not self.SKU_PATTERN.match(sku):
                    raise ValueError(
                        f"item[{i}] sku '{sku}' does not match pattern {self.SKU_PATTERN.pattern}"
                    )

                # Rule 4: quantity_on_hand phải là số >= 0
                qty = item.get("quantity_on_hand", 0)
                if not isinstance(qty, (int, float)):
                    raise ValueError(
                        f"item[{i}] invalid quantity_on_hand type: {type(qty).__name__}"
                    )
                if qty < 0:
                    raise ValueError(
                        f"item[{i}] quantity_on_hand must be >= 0, got {qty}"
                    )

                # Rule 5: reorder_point, max_stock_level phải là số >= 0
                for field_name in ("reorder_point", "max_stock_level"):
                    val = item.get(field_name, 0)
                    if not isinstance(val, (int, float)):
                        raise ValueError(
                            f"item[{i}] invalid {field_name} type: {type(val).__name__}"
                        )
                    if val < 0:
                        raise ValueError(
                            f"item[{i}] {field_name} must be >= 0, got {val}"
                        )

        logger.debug(f"InventoryGuardrails: input validation passed for task {request.task_id}")

    def check_permission(self, request: TaskRequest) -> None:
        """Kiểm tra permission scope — inventory là read-only.

        Inventory Monitor chỉ được:
        - Read stock levels
        - Generate alerts (based on thresholds)
        - Compute summaries

        Không được:
        - Modify inventory data (add/update/delete items in DB)
        - Place orders
        - Modify stock levels
        - Execute transactions
        """
        action = request.action

        if action not in self.VALID_ACTIONS:
            raise PermissionError(
                f"unauthorized action for inventory node: {action!r}"
            )

        # Inventory node là read-only — không có write actions
        write_actions = frozenset({
            "supply_chain.update_inventory",
            "supply_chain.adjust_stock",
            "supply_chain.place_order",
            "supply_chain.create_item",
            "supply_chain.delete_item",
        })

        if action in write_actions:
            raise PermissionError(
                f"inventory node cannot perform write action: {action!r}"
            )

        logger.debug(f"InventoryGuardrails: permission check passed for task {request.task_id}")

    def validate_output(self, response: Any) -> None:
        """Validate output từ inventory node.

        Raises:
            ValueError: Nếu output structure không hợp lệ.
        """
        # Response phải có status và result
        if not hasattr(response, "status") or not hasattr(response, "result"):
            raise ValueError(
                "inventory output must have 'status' and 'result' attributes"
            )

        result = response.result
        if not isinstance(result, dict):
            raise ValueError(
                f"invalid inventory result type: expected dict, got {type(result).__name__}"
            )

        # result có thể chứa: inventory_summary, snapshot, alerts
        # Không có rule bắt buộc — inventory node có thể trả về summary hoặc alerts tùy action

        # Nếu có alerts, phải là list các dict
        alerts = result.get("alerts")
        if alerts is not None:
            if not isinstance(alerts, list):
                raise ValueError(
                    f"invalid alerts type: expected list, got {type(alerts).__name__}"
                )
            for i, alert in enumerate(alerts):
                if not isinstance(alert, dict):
                    raise ValueError(
                        f"invalid alert[{i}] type: expected dict, got {type(alert).__name__}"
                    )
                if "alert_type" not in alert:
                    raise ValueError(f"alert[{i}] missing alert_type")
                if "sku" not in alert:
                    raise ValueError(f"alert[{i}] missing sku")

        # Nếu có inventory_summary, phải là dict
        summary = result.get("inventory_summary")
        if summary is not None and not isinstance(summary, dict):
            raise ValueError(
                f"invalid inventory_summary type: expected dict, got {type(summary).__name__}"
            )

        logger.debug(f"InventoryGuardrails: output validation passed")
