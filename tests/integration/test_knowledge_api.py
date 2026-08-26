"""Phase 2 Task 2.5 — /v1/knowledge/* API routes.

Uses sqlite + aiosqlite with tables created directly; the app's global
session factory is pointed at the test database via Settings override.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

import packages.database.session as session_mod
import packages.config.settings as settings_mod
from apps.api.main import create_app
from packages.config.settings import Settings, LLMProviderKind
from packages.database import models
from packages.database.base import Base
from packages.database.session import get_session_factory


@ pytest.fixture()
def client(tmp_path, monkeypatch):
    # Fresh module state per test: point the global engine at a temp sqlite db.
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'k.db').as_posix()}"
    # Provide legacy tenant_api_keys for backward compatibility
    get_session_factory(
        Settings(
            database_url=url,
            llm_provider=LLMProviderKind.MOCK,
            tenant_api_keys={
                "tenant-key-a": "00000000-0000-0000-0000-000000000001",
            },
        )
    )

    # Point the cached settings singleton at our test configuration
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(live, "tenant_api_keys", {
        "tenant-key-a": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(live, "rate_limit_per_minute", 1000)

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


def test_ingest_query_delete_roundtrip(client) -> None:
    resp = client.post(
        "/v1/knowledge/ingest",
        json={
            "title": "Refunds",
            "content": "We process refunds within 14 days of purchase. "
            "Shipping takes 3-5 business days.",
        },
        headers={"X-API-Key": "tenant-key-a"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chunk_count"] >= 1
    doc_id = data["document_id"]

    listing = client.get(
        "/v1/knowledge/documents", headers={"X-API-Key": "tenant-key-a"}
    ).json()
    assert any(d["id"] == doc_id for d in listing["documents"])

    q = client.post(
        "/v1/knowledge/query",
        json={"question": "refunds within 14 days"},
        headers={"X-API-Key": "tenant-key-a"},
    )
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["answer"]
    assert isinstance(body["citations"], list)

    d = client.delete(
        f"/v1/knowledge/documents/{doc_id}", headers={"X-API-Key": "tenant-key-a"}
    )
    assert d.status_code == 200, d.text
    assert d.json()["deleted"] is True

    d2 = client.delete(
        f"/v1/knowledge/documents/{doc_id}", headers={"X-API-Key": "tenant-key-a"}
    )
    assert d2.status_code == 404


def test_ingest_uses_default_org(client) -> None:
    resp = client.post(
        "/v1/knowledge/ingest", json={"title": "x", "content": "y"}, headers={"X-API-Key": "tenant-key-a"}
    )
    assert resp.status_code == 200


def test_validation_empty_content(client) -> None:
    resp = client.post(
        "/v1/knowledge/ingest", json={"title": "x", "content": ""}, headers={"X-API-Key": "tenant-key-a"}
    )
    assert resp.status_code == 422
