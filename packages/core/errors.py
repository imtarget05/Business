"""Standardized application error model (STEP 0.9).

Every error maps to a stable machine-readable `code` and is rendered to
clients as::

    {"error": {"code": "AGENT_TIMEOUT", "message": "...", "task_id": "..."}}

Internal stack traces are never exposed to clients.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {}  # populated below


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    TASK_STATE_ERROR = "TASK_STATE_ERROR"
    ROUTING_ERROR = "ROUTING_ERROR"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HANDOFF_DEPTH_EXCEEDED = "HANDOFF_DEPTH_EXCEEDED"
    HANDOFF_CYCLE_DETECTED = "HANDOFF_CYCLE_DETECTED"


_HTTP_STATUS = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTHENTICATION_ERROR: 401,
    ErrorCode.AUTHORIZATION_ERROR: 403,
    ErrorCode.AGENT_NOT_FOUND: 404,
    ErrorCode.AGENT_UNAVAILABLE: 503,
    ErrorCode.AGENT_TIMEOUT: 504,
    ErrorCode.LLM_PROVIDER_ERROR: 502,
    ErrorCode.DATABASE_ERROR: 503,
    ErrorCode.TASK_STATE_ERROR: 409,
    ErrorCode.ROUTING_ERROR: 422,
    ErrorCode.TOOL_EXECUTION_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.HANDOFF_DEPTH_EXCEEDED: 422,
    ErrorCode.HANDOFF_CYCLE_DETECTED: 422,
}

HTTP_STATUS_BY_CODE = dict(_HTTP_STATUS)


class BusinessOpsError(Exception):
    """Base class for all domain errors."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        task_id: UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.task_id = task_id
        self.details = details or {}
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_CODE[self.code]

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
        }
        if self.task_id is not None:
            payload["task_id"] = str(self.task_id)
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(BusinessOpsError):
    code = ErrorCode.VALIDATION_ERROR
    default_message = "Request validation failed"


class AuthenticationError(BusinessOpsError):
    code = ErrorCode.AUTHENTICATION_ERROR
    default_message = "Authentication required"


class AuthorizationError(BusinessOpsError):
    code = ErrorCode.AUTHORIZATION_ERROR
    default_message = "Not authorized to perform this action"


class AgentNotFoundError(BusinessOpsError):
    code = ErrorCode.AGENT_NOT_FOUND
    default_message = "No agent found for the requested capability"


class AgentUnavailableError(BusinessOpsError):
    code = ErrorCode.AGENT_UNAVAILABLE
    default_message = "Agent is registered but not currently available"


class AgentTimeoutError(BusinessOpsError):
    code = ErrorCode.AGENT_TIMEOUT
    default_message = "Agent execution timed out"


class LLMProviderError(BusinessOpsError):
    code = ErrorCode.LLM_PROVIDER_ERROR
    default_message = "LLM provider call failed"


class AgentExecutionError(BusinessOpsError):
    """Tool dispatch or agent-loop execution failure."""

    code = ErrorCode.INTERNAL_ERROR
    default_message = "Agent execution failed"


class DatabaseError(BusinessOpsError):
    code = ErrorCode.DATABASE_ERROR
    default_message = "Database operation failed"


class TaskStateError(BusinessOpsError):
    code = ErrorCode.TASK_STATE_ERROR
    default_message = "Illegal task state transition"


class RoutingError(BusinessOpsError):
    code = ErrorCode.ROUTING_ERROR
    default_message = "Task could not be routed to an agent"


class ToolExecutionError(BusinessOpsError):
    """Tool dispatch or agent-loop execution failure."""

    code = ErrorCode.TOOL_EXECUTION_ERROR
    default_message = "Tool execution failed"


class HandoffDepthExceededError(BusinessOpsError):
    """Raised when the maximum handoff depth is exceeded."""

    code = ErrorCode.HANDOFF_DEPTH_EXCEEDED
    default_message = "Maximum handoff depth exceeded"


class HandoffCycleDetectedError(BusinessOpsError):
    """Raised when a handoff cycle is detected (e.g., A -> B -> A)."""

    code = ErrorCode.HANDOFF_CYCLE_DETECTED
    default_message = "Handoff cycle detected"


class NotFoundError(BusinessOpsError):
    code = ErrorCode.AGENT_NOT_FOUND  # Maps to HTTP 404
    default_message = "Resource not found"




