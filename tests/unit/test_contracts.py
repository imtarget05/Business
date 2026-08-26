"""Agent Contract validation tests (STEP 0.3)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from packages.contracts.enums import AgentResponseStatus, Domain, TaskStatus
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskContext,
    TaskRequest,
)


def test_task_request_defaults() -> None:
    req = TaskRequest(
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": "What is our refund policy?"},
    )
    assert req.task_id is not None
    assert req.context.channel == "api"
    assert TaskRequest.model_validate(req.model_dump(mode="json"))


def test_task_request_rejects_bad_action() -> None:
    with pytest.raises(PydanticValidationError):
        TaskRequest(domain=Domain.SUPPORT, action="BAD ACTION", payload={})


def test_agent_response_success_with_error_is_invalid() -> None:
    with pytest.raises(PydanticValidationError):
        AgentResponse(
            task_id=uuid4(),
            agent="knowledge-v1",
            status=AgentResponseStatus.SUCCESS,
            error=ErrorDetail(code="X", message="boom"),
        )


def test_agent_response_failure_requires_error() -> None:
    with pytest.raises(PydanticValidationError):
        AgentResponse(
            task_id=uuid4(),
            agent="knowledge-v1",
            status=AgentResponseStatus.FAILED,
        )


def test_agent_response_all_statuses_validatable() -> None:
    for status in AgentResponseStatus:
        resp = AgentResponse(
            task_id=uuid4(),
            agent="support-v1",
            status=status,
            error=None if status == AgentResponseStatus.SUCCESS else ErrorDetail(
                code="E", message="m"
            ),
            confidence=0.9 if status == AgentResponseStatus.SUCCESS else 0.0,
        )
        assert resp.status == status


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(PydanticValidationError):
        AgentResponse(
            task_id=uuid4(),
            agent="knowledge-v1",
            status=AgentResponseStatus.SUCCESS,
            confidence=1.5,
        )


# ---------------------------------------------------------------------------
# STEP 0.4 — registry contract validation
# ---------------------------------------------------------------------------


def test_descriptor_capabilities_must_match_domain() -> None:
    with pytest.raises(PydanticValidationError):
        AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            capabilities=frozenset({"support.triage"}),
        )


def test_descriptor_capability_format_validated() -> None:
    with pytest.raises(PydanticValidationError):
        AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            capabilities=frozenset({"not a capability"}),
        )


def test_qualified_name() -> None:
    d = AgentDescriptor(name="support", domain=Domain.SUPPORT, version="2")
    assert d.qualified_name == "support-v2"


def test_task_status_enum_complete() -> None:
    expected = {
        "pending", "classifying", "routing", "running", "validating",
        "completed", "failed", "escalated", "cancelled", "dead_lettered",
    }
    assert {s.value for s in TaskStatus} == expected


def test_context_channel_values() -> None:
    ctx = TaskContext(channel="n8n")
    assert ctx.channel == "n8n"
