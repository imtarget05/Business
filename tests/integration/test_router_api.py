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
