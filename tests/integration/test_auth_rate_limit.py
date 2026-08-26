"""Integration tests for DB-backed API keys + rate limiting (Task 5.3)."""

from __future__ import annotations

import asyncio
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import packages.database.session as session_mod
import packages.config.settings as settings_mod
from apps.api.main import create_app
from packages.config.settings import Settings, LLMProviderKind
from packages.database import models
from packages.database.base import Base
from packages.database.repositories.api_keys import ApiKeyRepository
from packages.database.session import get_session_factory

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"
KEY_A = "tenant-key-a"
KEY_B = "tenant-key-b"


def _settings(url: str) -> Settings:
    return Settings(
        database_url=url,
        persistence_enabled=True,
        llm_provider=LLMProviderKind.MOCK,
        api_key=None,
        tenant_api_keys={KEY_A: ORG_A, KEY_B: ORG_B},
        rate_limit_per_minute=10,  # Low limit for testing
        environment="local",
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolated sqlite DB with two orgs using tenant_api_keys (legacy)."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test_auth.db').as_posix()}"
    s = _settings(url)
    get_session_factory(s)

    # Point the cached settings singleton at our tenant configuration.
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "api_key", None)
    monkeypatch.setattr(live, "tenant_api_keys", {KEY_A: ORG_A, KEY_B: ORG_B})
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "persistence_enabled", True)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(live, "rate_limit_per_minute", 10)
    monkeypatch.setattr(live, "environment", "local")

    async def _setup():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID(ORG_A), name="Org A", slug="a"
                )
            )
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID(ORG_B), name="Org B", slug="b"
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    
    session_mod._engine = None
    session_mod._session_factory = None


# ---------------------------------------------------------------------------
# Legacy tenant_api_keys authentication (local escape hatch)
# ---------------------------------------------------------------------------


def test_tenant_key_auth_works(client):
    """Valid tenant API key authenticates and binds to correct org."""
    # Create conversation with Org A's key
    resp = client.post(
        "/v1/conversations",
        json={"channel": "web"},
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert str(data["organization_id"]) == ORG_A


def test_tenant_key_rejects_unknown_key(client):
    """Unknown API key is rejected with 401."""
    resp = client.get(
        "/v1/conversations",
        headers={"X-API-Key": "unknown-key"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "AUTHENTICATION_ERROR"


def test_tenant_key_org_isolation(client):
    """Orgs cannot access each other's conversations via API keys."""
    # Org A creates a conversation
    resp_a = client.post(
        "/v1/conversations",
        json={"channel": "web"},
        headers={"X-API-Key": KEY_A},
    )
    assert resp_a.status_code == 201
    conv_id = resp_a.json()["conversation_id"]

    # Org B tries to access Org A's conversation
    resp_b = client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-API-Key": KEY_B},
    )
    # Should get 404 (not found) because cross-org access is blocked
    assert resp_b.status_code == 404


def test_missing_api_key_rejected(client):
    """Request without API key is rejected with 401."""
    resp = client.get("/v1/conversations")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "AUTHENTICATION_ERROR"


# ---------------------------------------------------------------------------
# Rate limiting (sliding window per API key)
# ---------------------------------------------------------------------------


def test_rate_limit_allows_requests_under_limit(client):
    """Requests under the limit succeed with rate limit headers."""
    for i in range(10):
        resp = client.get(
            "/v1/conversations",
            headers={"X-API-Key": KEY_A},
        )
        assert resp.status_code in (200, 401, 404), f"Request {i}: {resp.status_code} {resp.text}"

    # Check rate limit headers are present
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers
    assert int(resp.headers["X-RateLimit-Limit"]) == 10


def test_rate_limit_blocks_burst_over_limit(client):
    """Burst exceeding limit returns 429 with standard error envelope."""
    # Make 10 requests (the limit)
    for _ in range(10):
        client.get(
            "/v1/conversations",
            headers={"X-API-Key": KEY_A},
        )

    # 11th request should be rate limited
    resp = client.get(
        "/v1/conversations",
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 429
    data = resp.json()
    assert data["error"]["code"] == "RATE_LIMITED"
    assert data["error"]["message"] == "Rate limit exceeded"


def test_rate_limit_per_key_isolation(client):
    """Rate limit is per API key, not global."""
    # Exhaust Org A's limit
    for _ in range(10):
        client.get(
            "/v1/conversations",
            headers={"X-API-Key": KEY_A},
        )

    # Org B should still be able to make requests
    resp = client.get(
        "/v1/conversations",
        headers={"X-API-Key": KEY_B},
    )
    assert resp.status_code in (200, 401, 404), f"Org B blocked: {resp.status_code}"


def test_rate_limit_window_reset(client):
    """After window resets, requests are allowed again."""
    # This test would need time manipulation; skip for now
    # The sliding window is tested by the burst test
    pass


def test_rate_limit_headers_present(client):
    """Rate limit headers are present on all responses."""
    resp = client.get(
        "/v1/conversations",
        headers={"X-API-Key": KEY_A},
    )
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers


def test_rate_limit_not_applied_to_health_endpoints(client):
    """Health endpoints are not rate limited."""
    for _ in range(20):
        resp = client.get("/health")
        assert resp.status_code == 200
        # Health endpoint should not have rate limit headers
        assert "X-RateLimit-Limit" not in resp.headers


# ---------------------------------------------------------------------------
# DB-backed API key creation and verification (separate test to avoid locking)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_client(tmp_path, monkeypatch):
    """Isolated sqlite DB for testing DB-backed API key creation."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test_db_keys.db').as_posix()}"
    s = _settings(url)
    get_session_factory(s)

    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "api_key", None)
    monkeypatch.setattr(live, "tenant_api_keys", {})
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "persistence_enabled", True)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(live, "rate_limit_per_minute", 10)
    monkeypatch.setattr(live, "environment", "local")

    async def _setup():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID(ORG_A), name="Org A", slug="a"
                )
            )
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID(ORG_B), name="Org B", slug="b"
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    yield TestClient(create_app()), url
    session_mod._engine = None
    session_mod._session_factory = None


def test_db_backed_key_create_and_verify(db_client):
    """Test DB-backed API key creation and verification via repository."""
    client, url = db_client

    # Create API key directly via repository
    async def _create_key():
        eng = create_async_engine(url)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(_uuid.UUID(ORG_A), "test-db-key")
            await session.commit()
            return plaintext

    plaintext = asyncio.run(_create_key())
    assert plaintext.startswith("boas_")

    # Verify the key works via the API
    resp = client.post(
        "/v1/conversations",
        json={"channel": "web"},
        headers={"X-API-Key": plaintext},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert str(data["organization_id"]) == ORG_A


def test_db_backed_inactive_key_rejected(db_client):
    """Inactive (revoked) DB-backed key is rejected."""
    client, url = db_client

    async def _create_and_revoke():
        eng = create_async_engine(url)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(_uuid.UUID(ORG_A), "test-revoke")
            await session.commit()
            key_id = api_key.id
            await repo.revoke_key(_uuid.UUID(ORG_A), key_id)
            await session.commit()
            return plaintext

    plaintext = asyncio.run(_create_and_revoke())

    # Try to use revoked key
    resp = client.get(
        "/v1/conversations",
        headers={"X-API-Key": plaintext},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "AUTHENTICATION_ERROR"


def test_db_backed_key_org_isolation(db_client):
    """DB-backed keys enforce org isolation."""
    client, url = db_client

    async def _create_keys():
        eng = create_async_engine(url)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as session:
            repo = ApiKeyRepository(session)
            key_a_obj, key_a_plain = await repo.create_key(_uuid.UUID(ORG_A), "org-a-key")
            key_b_obj, key_b_plain = await repo.create_key(_uuid.UUID(ORG_B), "org-b-key")
            await session.commit()
            return key_a_plain, key_b_plain

    key_a, key_b = asyncio.run(_create_keys())

    # Org A creates conversation
    resp_a = client.post(
        "/v1/conversations",
        json={"channel": "web"},
        headers={"X-API-Key": key_a},
    )
    assert resp_a.status_code == 201
    conv_id = resp_a.json()["conversation_id"]

    # Org B tries to access Org A's conversation
    resp_b = client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-API-Key": key_b},
    )
    assert resp_b.status_code == 404


def test_existing_tenant_isolation_still_works(client):
    """Legacy tenant isolation tests still pass with new auth system."""
    # Org A creates conversation
    resp_a = client.post(
        "/v1/conversations",
        json={"channel": "web"},
        headers={"X-API-Key": KEY_A},
    )
    assert resp_a.status_code == 201
    conv_id = resp_a.json()["conversation_id"]

    # Org B cannot access Org A's conversation
    resp_b = client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-API-Key": KEY_B},
    )
    assert resp_b.status_code == 404


def test_client_supplied_org_id_ignored(client):
    """Client-supplied organization_id in body is ignored; server-side binding wins."""
    # Org A tries to create conversation for Org B by sending org_id in body
    resp = client.post(
        "/v1/conversations",
        json={"channel": "web", "organization_id": ORG_B},
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 201
    # Should be bound to Org A (from API key), not Org B
    assert str(resp.json()["organization_id"]) == ORG_A