"""Unit tests: Root Cause Agent (Phase 3)."""

from __future__ import annotations

from uuid import uuid4

from agents.root_cause import create_root_cause_agent
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskRequest
from packages.llm.mock import MockLLMProvider


def _req(task_id) -> TaskRequest:
    return TaskRequest(
        task_id=task_id,
        domain=Domain.OPS,
        action="root_cause",
        payload={"text": "why did the task fail?"},
    )


class TestRootCauseAgent:
    async def test_no_evidence_escalates(self) -> None:
        agent = create_root_cause_agent(llm=MockLLMProvider())
        resp = await agent.handle(_req(uuid4()))
        assert resp.status == AgentResponseStatus.ESCALATED
        assert resp.error.code == "EVIDENCE_UNAVAILABLE"

    async def test_with_evidence_returns_analysis(self) -> None:
        agent = create_root_cause_agent(llm=MockLLMProvider())
        tid = uuid4()
        agent.set_audit_events(
            [{"task_id": str(tid), "event": "action_failed", "payload": {"capability": "gmail.send"}}]
        )
        resp = await agent.handle(_req(tid))
        assert resp.status == AgentResponseStatus.SUCCESS
        assert resp.result["evidence_count"] == 1

    async def test_get_metrics_capability(self) -> None:
        agent = create_root_cause_agent(llm=MockLLMProvider())
        req = TaskRequest(
            task_id=uuid4(), domain=Domain.OPS, action="get_metrics", payload={}
        )
        resp = await agent.handle(req)
        assert resp.status == AgentResponseStatus.SUCCESS
        assert "metrics" in resp.result
