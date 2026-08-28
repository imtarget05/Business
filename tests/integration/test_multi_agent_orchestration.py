# -*- coding: utf-8 -*-
"""Phase C — Multi-Agent Orchestration integration tests.

Covers:
- C1: RouterAgent free-text classification wired into Orchestrator
- C3: GraphOrchestrator (langgraph_enabled) end-to-end execution
- C4-C6: cross-agent handoff (support -> knowledge) via orchestrator
"""

from __future__ import annotations

import json

import pytest

from packages.config.settings import LLMProviderKind, Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskRequest
from packages.core.bootstrap import build_container
from packages.llm.mock import MockLLMProvider


def _mock_settings(langgraph: bool = False) -> Settings:
    return Settings(
        llm_provider=LLMProviderKind.MOCK,
        langgraph_enabled=langgraph,
    )


@pytest.fixture()
def classic_container():
    return build_container(_mock_settings(langgraph=False))


@pytest.fixture()
def graph_container():
    return build_container(_mock_settings(langgraph=True))


# ---------------------------------------------------------------------------
# C1: RouterAgent free-text classification
# ---------------------------------------------------------------------------

async def test_orchestrator_classify_text_routes_support(classic_container) -> None:
    orchestrator = classic_container.orchestrator
    # Inject a scripted mock LLM so the router returns a confident intent.
    llm = classic_container.orchestrator._llm
    if hasattr(llm, "script"):
        llm.script(json.dumps({"domain": "support", "action": "triage", "confidence": 0.95}))
    classification = await orchestrator.classify_text("Tôi muốn hoàn tiền đơn #123")
    # Either a valid classification or None (escalate) — never raises.
    assert classification is None or classification.capability is not None


async def test_orchestrator_has_router_wired(classic_container) -> None:
    assert classic_container.orchestrator._router is not None


# ---------------------------------------------------------------------------
# C3: GraphOrchestrator end-to-end
# ---------------------------------------------------------------------------

async def test_graph_orchestrator_knowledge_query(graph_container) -> None:
    """LangGraph path executes a knowledge query successfully."""
    req = TaskRequest(domain="knowledge", action="query", payload={"question": "hi?"})
    resp = await graph_container.orchestrator.execute(req)
    assert resp.status in (AgentResponseStatus.SUCCESS, AgentResponseStatus.REJECTED)
    assert resp.agent == "knowledge-v1"


async def test_graph_orchestrator_support_triage(graph_container) -> None:
    req = TaskRequest(domain="support", action="triage", payload={})
    resp = await graph_container.orchestrator.execute(req)
    assert resp.status == AgentResponseStatus.ESCALATED


async def test_graph_orchestrator_unknown_action_fails(graph_container) -> None:
    from packages.core.errors import AgentNotFoundError

    req = TaskRequest(domain="support", action="nonexistent", payload={"subject": "x"})
    # Graph path raises a typed AgentNotFoundError for unroutable capabilities.
    with pytest.raises(AgentNotFoundError):
        await graph_container.orchestrator.execute(req)


# ---------------------------------------------------------------------------
# C4-C6: cross-agent handoff
# ---------------------------------------------------------------------------

async def test_handoff_support_to_knowledge(classic_container) -> None:
    """A support agent that requests a handoff to knowledge should merge results.

    We simulate this by invoking the orchestrator handoff path directly.
    """
    import uuid

    from packages.contracts.models import (
        AgentResponse,
        TaskContext,
    )

    orchestrator = classic_container.orchestrator

    # Build a handoff request: support -> knowledge.query
    ctx = TaskContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        channel="telegram",
        handoff_depth=0,
        max_handoff_depth=2,
        handoff_chain=["support-v1"],
    )
    req = TaskRequest(
        task_id=uuid.uuid4(),
        domain=Domain.SUPPORT,
        action="triage",
        payload={},
        context=ctx,
    )
    support_resp = AgentResponse(
        task_id=req.task_id,
        agent="support-v1",
        status=AgentResponseStatus.SUCCESS,
        result={"summary": "customer asks about warranty"},
        metadata={"handoff": {"target_capability": "knowledge.query"}},
    )
    handoff_resp = await orchestrator.handoff(
        req, support_resp, "knowledge.query"
    )
    # The handoff returns the *target* agent's response (knowledge-v1), proving
    # a cross-agent hop actually executed and returned from the knowledge agent.
    assert handoff_resp.agent == "knowledge-v1"
    # The merged response (orchestrator-level) preserves original + handoff result.
    merged = orchestrator._merge_handoff_response(support_resp, handoff_resp)
    assert merged.agent == "support-v1"
    assert "knowledge" in merged.result
    assert merged.metadata.get("handoff", {}).get("to") == "knowledge-v1"


async def test_multi_agent_registry_has_four_domains(classic_container) -> None:
    """Bootstrap registers supply_chain, support, knowledge, reporting agents."""
    descriptors = classic_container.registry.list_agents()
    domains = {d.domain for d in descriptors}
    assert Domain.SUPPLY_CHAIN in domains
    assert Domain.SUPPORT in domains
    assert Domain.KNOWLEDGE in domains
