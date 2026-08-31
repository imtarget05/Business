"""Integration tests for the feedback API (learning loop, ADR-010).

The /v1/feedback endpoints are protected by the API-key auth middleware, so
authenticated cases supply a valid ``X-API-Key`` resolved via the LOCAL
``tenant_api_keys`` fallback. Unauthenticated calls must return 401.

Uses the same sqlite-backed app fixture as test_api.py (no live Postgres).
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
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
    import os

    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh module state per test: point the global engine at a temp sqlite db."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    settings = Settings(
        database_url=url,
        persistence_enabled=True,
        llm_provider=LLMProviderKind.MOCK,
        tenant_api_keys={
            "test-key": "00000000-0000-0000-0000-000000000001",
        },
    )
    get_session_factory(settings)

    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "persistence_enabled", True)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(
        live,
        "tenant_api_keys",
        {
            "test-key": "00000000-0000-0000-0000-000000000001",
        },
    )
    monkeypatch.setattr(live, "rate_limit_per_minute", 1000)

    from packages.core.bootstrap import set_container

    set_container(None)

    async def _setup() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    name="Pilot Org",
                    slug="pilot",
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    yield TestClient(create_app())
    session_mod._engine = None
    session_mod._session_factory = None


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key"}


def test_feedback_no_auth_returns_401(client) -> None:
    """Missing API key -> 401 (fail-closed auth)."""
    resp = client.post("/v1/feedback", json={"task_id": "abc123def456"})
    assert resp.status_code == 401, resp.text


def test_feedback_stats_no_auth_returns_401(client) -> None:
    """GET /stats without API key -> 401."""
    resp = client.get("/v1/feedback/stats")
    assert resp.status_code == 401, resp.text


def test_feedback_submit_201(client, auth_headers) -> None:
    """Valid feedback (authenticated) is recorded with 201."""
    body = {
        "task_id": "abc123def456",
        "rating": "up",
        "corrected_capability": "research.web_search",
        "comment": "user wanted a web search not arxiv",
        "source": "telegram",
    }
    resp = client.post("/v1/feedback", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["task_id"] == body["task_id"]


def test_feedback_missing_required_422(client, auth_headers) -> None:
    """Authenticated but missing required fields -> 422."""
    resp = client.post("/v1/feedback", json={}, headers=auth_headers)
    assert resp.status_code == 422, resp.text


def test_feedback_invalid_rating_422(client, auth_headers) -> None:
    """rating outside {up,down} -> 422."""
    resp = client.post(
        "/v1/feedback",
        json={"task_id": "abc123def456", "rating": "sideways"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


def test_feedback_invalid_capability_422(client, auth_headers) -> None:
    """corrected_capability without a dot -> 422."""
    resp = client.post(
        "/v1/feedback",
        json={"task_id": "abc123def456", "corrected_capability": "weirdformat"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


def test_feedback_stats_200(client, auth_headers) -> None:
    """Authenticated stats endpoint returns rule summary (200)."""
    resp = client.get("/v1/feedback/stats", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "rules_total" in data
    assert "rules" in data
    assert isinstance(data["rules"], list)
