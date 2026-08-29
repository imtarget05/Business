"""Unit tests for the centralized audit layer (ADR-011)."""

from __future__ import annotations

import pytest

from packages.core.audit import (
    AuditEvent,
    AuditService,
    InMemoryAuditService,
    RiskLevel,
    classify_risk,
    redact,
)


class TestClassifyRisk:
    @pytest.mark.parametrize(
        ("capability", "expected"),
        [
            ("knowledge.query", RiskLevel.READ),
            ("gmail.search", RiskLevel.READ),
            ("gmail.send", RiskLevel.WRITE),
            ("calendar.create_event", RiskLevel.WRITE),
            ("calendar.delete_event", RiskLevel.DESTRUCTIVE),
            ("supply_chain.rollback", RiskLevel.DESTRUCTIVE),
        ],
    )
    def test_classification(self, capability: str, expected: RiskLevel) -> None:
        assert classify_risk(capability) == expected


class TestRedact:
    def test_redacts_secret_keys(self) -> None:
        out = redact({"api_key": "sk-123", "query": "hello"})
        assert out["api_key"] == "***REDACTED***"
        assert out["query"] == "hello"

    def test_none_passthrough(self) -> None:
        assert redact(None) is None
        assert redact({}) == {}


class TestInMemoryAuditService:
    async def test_emit_records_event(self) -> None:
        svc = InMemoryAuditService()
        await svc.emit(
            AuditEvent.TASK_CREATED,
            resource_type="task",
            resource_id="t-1",
            task_id="t-1",
            risk_level=RiskLevel.READ,
            payload={"capability": "knowledge.query", "api_key": "sk-1"},
        )
        assert len(svc.events) == 1
        event = svc.events[0]
        assert event["event"] == "task_created"
        assert event["payload"]["capability"] == "knowledge.query"
        assert event["payload"]["api_key"] == "***REDACTED***"
        assert event["risk_level"] == RiskLevel.READ

    async def test_base_service_noop_without_session_factory(self) -> None:
        svc = AuditService(session_factory=None)
        # must not raise
        await svc.emit(
            AuditEvent.AGENT_STARTED,
            resource_type="task",
            resource_id="t-2",
        )
