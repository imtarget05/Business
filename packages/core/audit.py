"""Centralized append-only Audit Layer (ADR-011).

Audit != Logging. Logging answers "what happened at runtime"; audit answers
"who did what, to which target, under which policy, with which result".

Every meaningful action emits an immutable audit event persisted to the
`audit_logs` table (packages/database/models.py::AuditLog — append-only by
design: no updates, no deletes). Secret redaction is applied to payloads.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from packages.database.models import AuditLog
from packages.observability.logging import get_logger

logger = get_logger("audit")


class RiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class AuditEvent(StrEnum):
    """Canonical audit event set (closed vocabulary for queryability)."""

    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    AGENT_SELECTED = "agent_selected"
    AGENT_STARTED = "agent_started"
    TOOL_INVOKED = "tool_invoked"
    POLICY_EVALUATED = "policy_evaluated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    RETRY = "retry"
    HANDOFF = "handoff"
    AGENT_RESULT = "agent_result"
    REVIEWER_RESULT = "reviewer_result"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


_DESTRUCTIVE_PATTERN = re.compile(
    r"delete|destroy|rollback|drop|purge|remove|revoke", re.IGNORECASE
)


def classify_risk(capability: str) -> RiskLevel:
    """Deterministic risk classification from the capability string."""
    action = capability.rsplit(".", 1)[-1]
    if _DESTRUCTIVE_PATTERN.search(action):
        return RiskLevel.DESTRUCTIVE
    if re.search(r"send|create|apply|scale|deploy|write|update|draft|append", action):
        return RiskLevel.WRITE
    return RiskLevel.READ


_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"
)


def redact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact secret-looking values before persisting audit payloads."""
    if not payload:
        return payload
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if re.search(r"(?i)token|secret|password|api[_-]?key", key):
            cleaned[key] = "***REDACTED***"
        else:
            cleaned[key] = value
    return cleaned


class AuditService:
    """Append-only audit emitter. Fire-and-forget safe: audit failures are
    logged but never break the task pipeline."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory

    async def emit(
        self,
        event: AuditEvent | str,
        *,
        resource_type: str,
        resource_id: str,
        organization_id: UUID | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        risk_level: RiskLevel | str | None = None,
        payload: dict[str, Any] | None = None,
        task_id: UUID | str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Persist one audit record. Never raises into the caller pipeline."""
        body = redact(payload) or {}
        if task_id is not None:
            body["task_id"] = str(task_id)
        if agent_id is not None:
            body["agent_id"] = agent_id
        if trace_id is not None:
            body["trace_id"] = trace_id
        if risk_level is not None:
            body["risk_level"] = (
                risk_level.value if isinstance(risk_level, RiskLevel) else str(risk_level)
            )
        try:
            if self._session_factory is None:
                return  # no-op sink (local dev / tests without DB)
            async with self._session_factory() as session:
                session.add(
                    AuditLog(
                        id=uuid4(),
                        organization_id=organization_id,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        event=str(event.value if isinstance(event, AuditEvent) else event),
                        resource_type=resource_type,
                        resource_id=resource_id,
                        payload=body,
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — audit must never break pipeline
            logger.error(
                "audit_write_failed",
                extra={"event": str(event), "error": type(exc).__name__},
            )


class InMemoryAuditService(AuditService):
    """Test/dev sink that records events in memory instead of the DB."""

    def __init__(self) -> None:
        super().__init__(session_factory=None)
        self.events: list[dict[str, Any]] = []

    async def emit(self, event, **kwargs) -> None:  # type: ignore[override]
        body = redact(kwargs.pop("payload", None)) or {}
        self.events.append(
            {
                "event": str(event.value if isinstance(event, AuditEvent) else event),
                **{k: v for k, v in kwargs.items() if v is not None},
                "payload": body,
            }
        )


__all__ = [
    "AuditService",
    "InMemoryAuditService",
    "AuditEvent",
    "RiskLevel",
    "classify_risk",
    "redact",
]

