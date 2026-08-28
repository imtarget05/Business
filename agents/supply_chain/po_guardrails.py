"""Guardrails implementation for PO Agent.

Provides input validation, permission checks, output verification,
and tracing hooks for the PurchaseOrderAgent.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.supply_chain.po_agent import PurchaseOrderAgent
from packages.contracts.models import TaskRequest

logger = logging.getLogger(__name__)


class POAgentGuardrails:
    """Guardrails wrapper cho PO Agent.

    Triển khai 3 tầng guardrails:
    1. Input validation (email_content type, non-empty, size limit)
    2. Permission check (chỉ parse/classify/route, không send email/modify DB)
    3. Output verification (result structure cố định, không raw LLM response)
    """

    MAX_EMAIL_SIZE = 50_000  # characters

    def __init__(self, agent: PurchaseOrderAgent):
        self._agent = agent

    def validate_input(self, request: TaskRequest) -> None:
        """Validate TaskRequest input cho PO Agent.

        Raises:
            ValueError: Nếu input không hợp lệ.
        """
        payload = request.payload
        email_content = payload.get("email_content")

        # Rule 1: email_content phải là string
        if not isinstance(email_content, str):
            raise ValueError(
                f"invalid email_content type: expected str, got {type(email_content).__name__}"
            )

        # Rule 2: email_content không rỗng
        if not email_content.strip():
            raise ValueError("email_content must not be empty")

        # Rule 3: email_content size limit
        if len(email_content) > self.MAX_EMAIL_SIZE:
            raise ValueError(
                f"email_content too large: {len(email_content)} chars "
                f"(max {self.MAX_EMAIL_SIZE})"
            )

        # Rule 4: action phải là supported action
        if request.action not in self._agent.SUPPORTED_ACTIONS:
            raise ValueError(
                f"unsupported action: {request.action!r} "
                f"(supported: {self._agent.SUPPORTED_ACTIONS})"
            )

        logger.debug(f"POAgentGuardrails: input validation passed for task {request.task_id}")

    def check_permission(self, request: TaskRequest) -> None:
        """Kiểm tra permission scope cho PO Agent.

        Raises:
            PermissionError: Nếu request vi phạm permission scope.
        """
        # PO Agent chỉ được làm: parse, classify, route
        # Không được: send email, modify DB, create records, call external API
        action = request.action

        # Danh sách action hợp lệ
        allowed_actions = self._agent.SUPPORTED_ACTIONS

        if action not in allowed_actions:
            raise PermissionError(
                f"action {action!r} not in allowed scope {allowed_actions}"
            )

        # Kiểm tra thêm: payload có chứa các field không thuộc scope không
        # (ví dụ: có request để gửi email, modify DB → reject)
        disallowed_fields = {"send_email", "create_record", "update_db", "call_external_api"}
        payload = request.payload

        for field in disallowed_fields:
            if field in payload and payload[field]:
                raise PermissionError(
                    f"PO Agent không được phép thực hiện: {field}"
                )

        logger.debug(f"POAgentGuardrails: permission check passed for task {request.task_id}")

    def verify_output(self, response_any: Any) -> Any:
        """Verify response output structure.

        Args:
            response_any: Response từ agent (có thể là AgentResponse hoặc dict)

        Returns:
            Response đã verify (giữ nguyên nếu hợp lệ)

        Raises:
            ValueError: Nếu output không có cấu trúc hợp lệ.
        """
        # Nếu là AgentResponse, check structure
        if hasattr(response_any, "result"):
            result = response_any.result
            if not isinstance(result, dict):
                raise ValueError(f"result must be dict, got {type(result).__name__}")

            # Check required fields
            required_fields = ["po_number", "vendor", "items", "total", "route", "po_type"]
            missing = [f for f in required_fields if f not in result]
            if missing:
                raise ValueError(f"PO result missing required fields: {missing}")

            # Verify route value
            valid_routes = {
                "auto_approved",
                "approval_required_manager_a",
                "approval_required_manager_b",
            }
            if result.get("route") not in valid_routes:
                raise ValueError(
                    f"invalid route value: {result.get('route')!r} "
                    f"(valid: {valid_routes})"
                )

            logger.debug(
                f"POAgentGuardrails: output verification passed "
                f"po_number={result.get('po_number')}"
            )

        # Nếu là dict ( directly from agent), cũng check
        elif isinstance(response_any, dict):
            required_fields = ["po_number", "vendor", "items", "total", "route", "po_type"]
            missing = [f for f in required_fields if f not in response_any]
            if missing:
                raise ValueError(f"PO result missing required fields: {missing}")

        return response_any

    async def wrapped_handle(self, request: TaskRequest) -> Any:
        """Handle request với guardrails áp dụng.

        Returns:
            AgentResponse hoặc dict đã verify.
        """
        # Step 1: Validate input
        self.validate_input(request)

        # Step 2: Check permission
        self.check_permission(request)

        # Step 3: Call agent handle
        response = await self._agent.handle(request)

        # Step 4: Verify output
        return self.verify_output(response)


def create_guarded_agent(agent: PurchaseOrderAgent) -> POAgentGuardrails:
    """Create guardrails wrapper cho PO Agent.

    Args:
        agent: PurchaseOrderAgent instance.

    Returns:
        POAgentGuardrails wrapper.
    """
    return POAgentGuardrails(agent)
