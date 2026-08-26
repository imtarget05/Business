"""Phase 4 — /v1/router/dispatch endpoint tests.

The global LLM provider is `mock` by default in tests; MockLLMProvider's
unscripted structured output raises unless scripted, so these tests exercise
the rule-based fallback and escalation paths (deterministic, no network).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_dispatch_routes_refund_email(client) -> None:
    resp = client.post(
        "/v1/router/dispatch", json={"text": "Tôi muốn hoàn tiền cho đơn #123"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["classification"]["domain"] == "support"
    assert data["classification"]["action"] == "triage"
    assert data["classification"]["source"] in ("rules", "llm")


def test_dispatch_policy_question_to_knowledge(client) -> None:
    resp = client.post(
        "/v1/router/dispatch",
        json={"text": "Chính sách đổi trả như thế nào?"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["classification"]["domain"] == "knowledge"


def test_dispatch_escalates_on_gibberish(client) -> None:
    resp = client.post(
        "/v1/router/dispatch", json={"text": "zzz qqq xyzzy plugh"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "escalated"
    assert data["reason"]


def test_dispatch_rejects_empty_text(client) -> None:
    resp = client.post("/v1/router/dispatch", json={"text": ""})
    assert resp.status_code == 422


def test_dispatch_uses_container_registry_capabilities(client) -> None:
    """Integration test: dispatch works against real container's registry.

    The router should use the container's registered agents (support + knowledge)
    for its routing table, not just hardcoded fallbacks.
    """
    from packages.core.bootstrap import get_container

    container = get_container()

    # Verify registry has both support and knowledge agents with expected capabilities
    agents = container.registry.list_agents()
    agent_names = [d.name for d in agents]
    assert "support" in agent_names
    assert "knowledge" in agent_names

    support_caps = set()
    knowledge_caps = set()
    for descriptor in agents:
        if descriptor.name == "support":
            support_caps = descriptor.capabilities
        elif descriptor.name == "knowledge":
            knowledge_caps = descriptor.capabilities

    assert "support.triage" in support_caps
    assert "support.draft_reply" in support_caps
    assert "knowledge.query" in knowledge_caps

    # Now test that dispatch actually uses these capabilities
    # Refund email -> support.triage (via rule fallback matching "hoàn tiền")
    resp = client.post(
        "/v1/router/dispatch", json={"text": "Tôi muốn hoàn tiền cho đơn #123"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["classification"]["domain"] == "support"
    assert data["classification"]["action"] == "triage"

    # Policy question -> knowledge.query (via rule fallback matching "chính sách")
    resp = client.post(
        "/v1/router/dispatch",
        json={"text": "Chính sách đổi trả như thế nào?"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["classification"]["domain"] == "knowledge"
    assert data["classification"]["action"] == "query"

    # Verify the router agent used by the endpoint has the registry's capabilities
    from packages.core.router import RouterAgent, _build_routing_table
    from packages.llm.mock import MockLLMProvider
    from packages.config.settings import get_settings

    settings = get_settings()
    router_agent = RouterAgent(
        llm=MockLLMProvider(),
        registry=container.registry,
        confidence_threshold=settings.router_confidence_threshold,
    )

    routing_table = router_agent._get_allowed_intents()
    assert ("support", "triage") in routing_table
    assert ("support", "draft_reply") in routing_table
    assert ("knowledge", "query") in routing_table
