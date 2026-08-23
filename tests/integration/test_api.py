"""API tests: health, readiness, tasks, agents, error envelope.

No live DB required: /ready failure path is exercised via monkeypatch.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "X-Request-ID" in resp.headers


def test_ready_not_ready_when_db_down(monkeypatch) -> None:
    async def fail() -> bool:
        return False

    import apps.api.routes.health as health_module

    monkeypatch.setattr(health_module, "check_database", fail)
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "unavailable"


def test_create_task_happy_path() -> None:
    client = TestClient(create_app())
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "What is our refund policy?"},
        "context": {"channel": "dashboard"},
    }
    resp = client.post("/v1/tasks", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["agent"] == "knowledge-v1"
    assert data["task_id"]
    assert isinstance(data["citations"], list)


def test_list_agents_endpoint() -> None:
    client = TestClient(create_app())
    resp = client.get("/v1/agents")
    assert resp.status_code == 200
    names = {f"{a['name']}-v{a['version']}" for a in resp.json()["agents"]}
    assert {"knowledge-v1", "support-v1"} <= names


def test_validation_error_envelope() -> None:
    client = TestClient(create_app())
    resp = client.post("/v1/tasks", json={"action": "query"})  # domain missing
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"


def test_empty_payload_rejected_with_task_id() -> None:
    client = TestClient(create_app())
    resp = client.post(
        "/v1/tasks", json={"domain": "support", "action": "triage", "payload": {}}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["task_id"]
