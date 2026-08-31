"""API tests: health, readiness, tasks, agents, error envelope.

No live DB required: /ready failure path is exercised via monkeypatch.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import packages.config.settings as settings_mod
import packages.database.session as session_mod
from apps.api.main import create_app
from packages.config.settings import LLMProviderKind, Settings
from packages.database import models
from packages.database.base import Base
from packages.database.session import get_session_factory


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh module state per test: point the global engine at a temp sqlite db."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    # Provide legacy tenant_api_keys for backward compatibility
    settings = Settings(
        database_url=url,
        persistence_enabled=True,
        llm_provider=LLMProviderKind.MOCK,
        tenant_api_keys={
            "tenant-key-a": "00000000-0000-0000-0000-000000000001",
            "tenant-key-b": "00000000-0000-0000-0000-000000000002",
        },
    )
    get_session_factory(settings)

    # Point the cached settings singleton at our test configuration
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "persistence_enabled", True)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(
        live,
        "tenant_api_keys",
        {
            "tenant-key-a": "00000000-0000-0000-0000-000000000001",
            "tenant-key-b": "00000000-0000-0000-0000-000000000002",
        },
    )
    monkeypatch.setattr(live, "rate_limit_per_minute", 1000)

    # Force container rebuild with patched settings (singleton cache)
    from packages.core.bootstrap import set_container

    set_container(None)

    async def _setup() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Create kb_chunks table (used by KnowledgeBase) to avoid "database is locked" errors
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kb_chunks (
                        id VARCHAR(36) PRIMARY KEY,
                        doc_id VARCHAR(36) NOT NULL,
                        source_path TEXT NOT NULL,
                        title TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        embedding TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    name="Pilot Org",
                    slug="pilot",
                )
            )
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID("00000000-0000-0000-0000-000000000002"),
                    name="Pilot Org B",
                    slug="pilot-b",
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    yield TestClient(create_app())
    session_mod._engine = None
    session_mod._session_factory = None


def test_health_ok(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "X-Request-ID" in resp.headers


def test_ready_not_ready_when_db_down(client, monkeypatch) -> None:
    async def fail() -> bool:
        return False

    import apps.api.routes.health as health_module

    # Override the health check
    monkeypatch.setattr(health_module, "check_database", fail)
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "unavailable"


def test_create_task_happy_path(client) -> None:
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "What is our refund policy?"},
        "context": {"channel": "dashboard"},
    }
    resp = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Phase 2: no org/KB in this test => agent must refuse to guess (REJECTED).
    assert data["status"] in ("success", "rejected")
    assert data["agent"] == "knowledge-v1"
    assert data["task_id"]
    assert isinstance(data["citations"], list)
    if data["status"] == "success":
        assert data["result"]["answer"] == "no relevant information found"
        assert data["citations"] == []


def test_list_agents_endpoint(client) -> None:
    resp = client.get("/v1/agents", headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200
    names = {f"{a['name']}-v{a['version']}" for a in resp.json()["agents"]}
    assert {"knowledge-v1", "support-v1"} <= names


def test_validation_error_envelope(client) -> None:
    resp = client.post(
        "/v1/tasks", json={"action": "query"}, headers={"X-API-Key": "tenant-key-a"}
    )  # domain missing
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"


def test_empty_payload_rejected_with_task_id(client) -> None:
    resp = client.post(
        "/v1/tasks",
        json={"domain": "support", "action": "triage", "payload": {}},
        headers={"X-API-Key": "tenant-key-a"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["task_id"]


def test_task_timeline_returns_chronological_events(client) -> None:
    """Timeline endpoint returns events in chronological order from task, steps, agent_runs, audit_logs."""
    # Create a task
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "test timeline"},
        "context": {"channel": "dashboard"},
    }
    resp = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    # Fetch timeline
    resp = client.get(f"/v1/tasks/{task_id}/timeline", headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    timeline = data["timeline"]

    # Should have at least task creation event
    assert len(timeline) >= 1
    # First event should be task creation
    assert timeline[0]["stage"] == "task"
    assert timeline[0]["status"] == "created"
    # Events should be sorted by time ascending
    times = [e["time"] for e in timeline if e["time"]]
    assert times == sorted(times), "Timeline events must be chronologically ordered"


def test_task_timeline_cross_org_returns_404(client) -> None:
    """Cross-org access to timeline returns 404 (not 403/200)."""
    # Org A creates a task
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "org A task"},
        "context": {"channel": "dashboard"},
    }
    resp_a = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    assert resp_a.status_code == 200, resp_a.text
    task_id = resp_a.json()["task_id"]

    # Org B tries to access the timeline
    resp_b = client.get(f"/v1/tasks/{task_id}/timeline", headers={"X-API-Key": "tenant-key-b"})
    # Must return 404 (not found), not 403 (forbidden) or 200
    assert resp_b.status_code == 404, f"Expected 404, got {resp_b.status_code}: {resp_b.text}"


def test_task_timeline_includes_steps_and_runs(client) -> None:
    """Timeline includes step transitions and agent run events when they exist."""
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "timeline with steps"},
        "context": {"channel": "dashboard"},
    }
    resp = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    # The mock orchestrator creates steps; verify they appear in timeline
    resp = client.get(f"/v1/tasks/{task_id}/timeline", headers={"X-API-Key": "tenant-key-a"})
    assert resp.status_code == 200, resp.text
    timeline = resp.json()["timeline"]

    # Should have task creation + step events (classifying, routing, running, etc.)
    stages = {e["stage"] for e in timeline}
    assert "task" in stages
    assert "step" in stages
    # Agent runs may or may not be present depending on mock; at minimum steps should exist
