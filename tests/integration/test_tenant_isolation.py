"""Audit fix wave — tenant isolation, tool org injection, email allowlist,
dispatch persistence.

Covers:
- Tenant A cannot read tenant B's resources on any route, even when sending
  B's organization_id in the request body.
- Tools reject LLM-supplied organization_id that mismatches the server-side
  binding (and refuse to run without a binding).
- send_email_reply blocks recipients outside the conversation org's customer
  records / explicit allowlist when sending is enabled.
- POST /v1/router/dispatch persists a task row visible to the caller's org
  and invisible to other tenants.
- Fail-closed auth: app refuses to start without any API key outside local.
"""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import packages.config.settings as settings_mod
import packages.database.session as session_mod
from agents.support.tools import CreateTicketTool, SendEmailReplyTool
from apps.api.main import create_app
from packages.config.settings import LLMProviderKind, Settings
from packages.core.errors import ToolExecutionError
from packages.database import models
from packages.database.base import Base
from packages.database.session import get_session_factory

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"
KEY_A = "tenant-key-a"
KEY_B = "tenant-key-b"

ALL_TABLES = None  # create the full metadata (dependency order handled by SA)


def _settings(url: str) -> Settings:
    return Settings(
        database_url=url,
        persistence_enabled=True,
        llm_provider=LLMProviderKind.MOCK,
        api_key=None,
        tenant_api_keys={KEY_A: ORG_A, KEY_B: ORG_B},
        rate_limit_per_minute=1000,  # High limit for tests
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolated sqlite DB, two orgs, two tenant keys."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'tenants.db').as_posix()}"
    s = _settings(url)
    get_session_factory(s)

    # Point the cached settings singleton at our tenant configuration.
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "api_key", None)
    monkeypatch.setattr(live, "tenant_api_keys", {KEY_A: ORG_A, KEY_B: ORG_B})
    monkeypatch.setattr(live, "rate_limit_per_minute", 1000)
    # Routes read the cached singleton; mirror our sqlite/persistence config.
    monkeypatch.setattr(
        live, "database_url", f"sqlite+aiosqlite:///{(tmp_path / 'tenants.db').as_posix()}"
    )
    monkeypatch.setattr(live, "persistence_enabled", True)

    # Set rate limit env var before creating TestClient
    os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"

    async def _setup() -> None:
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
    yield TestClient(create_app())
    session_mod._engine = None
    session_mod._session_factory = None


# ---------------------------------------------------------------------------
# Cross-tenant isolation over HTTP
# ---------------------------------------------------------------------------


def _create_conversation(client, key, channel="web"):
    resp = client.post(
        "/v1/conversations",
        json={"channel": channel},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["conversation_id"]


def test_tenant_a_cannot_read_tenant_b_conversation(client):
    conv_a = _create_conversation(client, KEY_A)
    conv_b = _create_conversation(client, KEY_B)

    # Even sending B's org id in the body must not help tenant A.
    r = client.get(f"/v1/conversations/{conv_b}", headers={"X-API-Key": KEY_A})
    assert r.status_code == 404, r.text
    r = client.get(
        f"/v1/conversations/{conv_b}/messages",
        headers={"X-API-Key": KEY_A},
    )
    assert r.status_code == 404

    # Owner still sees it.
    r = client.get(f"/v1/conversations/{conv_a}", headers={"X-API-Key": KEY_A})
    assert r.status_code == 200


def test_client_supplied_org_id_ignored_on_create(client):
    """Tenant A creating a conversation while claiming B's org id in the body."""
    resp = client.post(
        "/v1/conversations",
        json={"channel": "web", "organization_id": ORG_B},
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # Bound to the CALLER's org (A), not the body-supplied B.
    assert str(data["organization_id"]) == ORG_A
    # And tenant B cannot see it.
    r = client.get(f"/v1/conversations/{data['conversation_id']}", headers={"X-API-Key": KEY_B})
    assert r.status_code == 404


def test_message_post_body_org_id_cannot_cross_tenants(client):
    conv_b = _create_conversation(client, KEY_B)
    resp = client.post(
        f"/v1/conversations/{conv_b}/messages",
        json={"content": "hi", "organization_id": ORG_B},
        headers={"X-API-Key": KEY_A},  # tenant A claims B's org in body
    )
    assert resp.status_code == 404, resp.text


def test_task_list_scoped_per_tenant(client):
    resp = client.post(
        "/v1/tasks",
        json={
            "domain": "knowledge",
            "action": "query",
            "payload": {"question": "refund policy?"},
            "context": {
                "channel": "dashboard",
                "organization_id": ORG_B,  # must be ignored
            },
        },
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    listed_a = client.get("/v1/tasks", headers={"X-API-Key": KEY_A}).json()
    ids_a = [t["task_id"] for t in listed_a["tasks"]]
    assert task_id in ids_a

    listed_b = client.get("/v1/tasks", headers={"X-API-Key": KEY_B}).json()
    assert task_id not in [t["task_id"] for t in listed_b["tasks"]]

    # Direct fetch cross-tenant -> 404 even with victim org id in query.
    r = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": KEY_B})
    assert r.status_code == 404
    r = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": KEY_A})
    assert r.status_code == 200


def test_dispatch_persists_task_row_visible_only_to_caller_org(client):
    resp = client.post(
        "/v1/router/dispatch",
        json={"text": "Tôi muốn hoàn tiền cho đơn #123", "organization_id": ORG_B},
        headers={"X-API-Key": KEY_A},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    task_id = data.get("task_id")
    assert task_id, data  # routed tasks always carry an id now

    r = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": KEY_A})
    assert r.status_code == 200
    assert r.json()["task"]["task_id"] == task_id

    # Other tenant cannot see the dispatched task.
    r = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": KEY_B})
    assert r.status_code == 404

    # Audit rows exist (steps recorded through the recorder).
    steps = client.get("/v1/steps", headers={"X-API-Key": KEY_A}).json()["steps"]
    assert any(s["task_id"] == task_id for s in steps)


# ---------------------------------------------------------------------------
# Tool-level org injection
# ---------------------------------------------------------------------------


@pytest.fixture()
async def tool_db(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'tools.db').as_posix()}"
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    await eng.dispose()


@pytest.mark.asyncio
async def test_create_ticket_rejects_mismatched_org_injection(tool_db):
    tool = CreateTicketTool(tool_db)
    tool.bind_organization(_uuid.UUID(ORG_A))
    with pytest.raises(ToolExecutionError):
        await tool.run(
            {
                "customer_id": str(_uuid.uuid4()),
                "subject": "s",
                "organization_id": ORG_B,  # LLM tries to smuggle another org
            }
        )


@pytest.mark.asyncio
async def test_create_ticket_rejects_unbound_tool(tool_db):
    tool = CreateTicketTool(tool_db)  # never bound
    with pytest.raises(ToolExecutionError):
        await tool.run({"customer_id": str(_uuid.uuid4()), "subject": "s"})


# ---------------------------------------------------------------------------
# Email recipient allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_blocks_foreign_recipient(tool_db, monkeypatch):
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "email_send_enabled", True)
    monkeypatch.setattr(live, "email_smtp_host", "smtp.invalid")
    monkeypatch.setattr(live, "email_recipient_allowlist", [])

    tool = SendEmailReplyTool(tool_db)
    tool.bind_organization(_uuid.UUID(ORG_A))

    with pytest.raises(ToolExecutionError):
        await tool.run(
            {
                "to_email": "stranger@evil.example",
                "subject": "s",
                "body_text": "b",
            }
        )


@pytest.mark.asyncio
async def test_send_email_allows_allowlisted_recipient_check(tool_db, monkeypatch):
    """The allowlist gate itself permits customer-record/allowlisted emails."""
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "email_recipient_allowlist", ["vip@example.com"])

    tool = SendEmailReplyTool(tool_db)
    tool.bind_organization(_uuid.UUID(ORG_A))

    assert await tool._recipient_allowed(_uuid.UUID(ORG_A), "vip@example.com", None)
    assert not await tool._recipient_allowed(_uuid.UUID(ORG_A), "nope@example.com", None)


@pytest.mark.asyncio
async def test_dry_run_unaffected_by_allowlist(monkeypatch):
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "email_send_enabled", False)
    tool = SendEmailReplyTool()
    result = await tool.run(
        {"to_email": "anyone@anywhere.example", "subject": "s", "body_text": "b"}
    )
    assert '"mode": "DRY_RUN"' in result or '"DRY_RUN"' in result


# ---------------------------------------------------------------------------
# Fail-closed startup
# ---------------------------------------------------------------------------


def test_app_refuses_to_start_without_key_outside_local(monkeypatch):
    from packages.config.settings import Environment

    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "environment", Environment.PRODUCTION)
    monkeypatch.setattr(live, "api_key", None)
    monkeypatch.setattr(live, "tenant_api_keys", {})
    with pytest.raises(RuntimeError):
        with TestClient(create_app()):
            pass


def test_unknown_tenant_key_rejected(client):
    resp = client.get("/v1/conversations", headers={"X-API-Key": "not-a-tenant-key"})
    assert resp.status_code == 401
