"""Orchestrator end-to-end tests with MockLLMProvider (no DB, no network)."""

from __future__ import annotations

import pytest

from packages.config.settings import LLMProviderKind, Settings
from packages.contracts.enums import AgentResponseStatus
from packages.contracts.models import TaskRequest
from packages.core.bootstrap import build_container
from packages.llm.mock import MockLLMProvider


@pytest.fixture()
def container():
    return build_container(Settings(llm_provider=LLMProviderKind.MOCK))


async def test_knowledge_query_success(container) -> None:
    """Phase 2: without an org-scoped knowledge base the agent must refuse to
    guess — it returns 'no relevant information found' rather than hallucinating
    (hard acceptance criterion), or REJECTED when no org context is provided."""
    req = TaskRequest(domain="knowledge", action="query", payload={"question": "hi?"})
    resp = await container.orchestrator.execute(req)
    assert resp.status in (
        AgentResponseStatus.SUCCESS,
        AgentResponseStatus.REJECTED,
    )
    assert resp.agent == "knowledge-v1"
    if resp.status == AgentResponseStatus.SUCCESS:
        assert resp.result["answer"] == "no relevant information found"
        assert resp.citations == []


async def test_knowledge_missing_question_rejected(container) -> None:
    req = TaskRequest(domain="knowledge", action="query", payload={})
    resp = await container.orchestrator.execute(req)
    # agent REJECTED is still a completed task lifecycle (validated output)
    assert resp.status == AgentResponseStatus.REJECTED


async def test_support_triage_escalates_without_subject(container) -> None:
    req = TaskRequest(domain="support", action="triage", payload={})
    resp = await container.orchestrator.execute(req)
    assert resp.status == AgentResponseStatus.ESCALATED


async def test_unknown_action_routes_to_error(container) -> None:
    req = TaskRequest(domain="support", action="nonexistent", payload={"subject": "x"})
    resp = await container.orchestrator.execute(req)
    assert resp.status == AgentResponseStatus.FAILED
    assert resp.error is not None
    assert resp.error.code == "AGENT_NOT_FOUND"


async def test_mock_llm_was_called(container) -> None:
    provider = container.orchestrator._llm
    assert isinstance(provider, MockLLMProvider)
    before = len(provider.calls)
    await container.orchestrator.execute(
        TaskRequest(domain="support", action="draft_reply", payload={"subject": "s"})
    )
    # LLM calls: (1) router/capability classification, (2) the agent tool loop,
    # (3) reflection auto-critique fired after the task resolves (ADR-010).
    assert len(provider.calls) == before + 3
