"""Adversarial tenant-isolation tests for Support Agent tools (Phase 5).

These harden the org-scoping guarantee: an LLM-supplied organization_id must
never override the server-side principal, and even DRY-RUN drafts must be bound
to the caller's org (a draft to another org's customer is still a leak).

No network; uses sqlite (aiosqlite) for the DB-backed _recipient_allowed path.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.support.tools import (
    LookupCustomerTool,
    SendEmailReplyTool,
    SendGmailReplyTool,
)
from packages.config.settings import get_settings
from packages.core.errors import ToolExecutionError
from packages.database.base import Base
from packages.database.models import Customer


def tmp_db() -> str:
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
async def sf():
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


# --- _OrgBoundTool._resolve_org unit ------------------------------------------


def test_resolve_org_rejects_mismatch():
    t = SendEmailReplyTool()
    t.bind_organization(uuid.uuid4())
    other = uuid.uuid4()
    with pytest.raises(ToolExecutionError, match="organization mismatch"):
        t._resolve_org({"organization_id": str(other)})


def test_resolve_org_rejects_unbound():
    t = SendEmailReplyTool()
    with pytest.raises(ToolExecutionError, match="no server-side organization"):
        t._resolve_org({})


def test_resolve_org_accepts_bound_without_arg():
    org = uuid.uuid4()
    t = SendEmailReplyTool()
    t.bind_organization(org)
    assert t._resolve_org({}) == org


def test_resolve_org_pops_supplied_matching_arg():
    org = uuid.uuid4()
    t = SendEmailReplyTool()
    t.bind_organization(org)
    args = {"organization_id": str(org), "x": 1}
    assert t._resolve_org(args) == org
    # supplied org id must be stripped so downstream code can't re-read it
    assert "organization_id" not in args


# --- DRY-RUN must be org-scoped -----------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_enforces_org_binding():
    """DRY-RUN without a bound org must fail (a draft is still tenant-scoped)."""
    tool = SendEmailReplyTool()
    # no bind_organization -> _resolve_org raises
    with pytest.raises(ToolExecutionError, match="no server-side organization"):
        await tool.run(
            {"to_email": "a@b.com", "subject": "s", "body_text": "b"}
        )


@pytest.mark.asyncio
async def test_dry_run_draft_carries_org_id():
    org = uuid.uuid4()
    tool = SendEmailReplyTool()
    tool.bind_organization(org)
    result = await tool.run(
        {"to_email": "a@b.com", "subject": "s", "body_text": "b"}
    )
    data = json.loads(result)
    assert data["mode"] == "DRY_RUN"
    assert data["organization_id"] == str(org)


@pytest.mark.asyncio
async def test_dry_run_rejects_llm_supplied_other_org():
    """An LLM trying to draft as a different org must be rejected."""
    org = uuid.uuid4()
    other = uuid.uuid4()
    tool = SendEmailReplyTool()
    tool.bind_organization(org)
    with pytest.raises(ToolExecutionError, match="organization mismatch"):
        await tool.run(
            {
                "to_email": "a@b.com",
                "subject": "s",
                "body_text": "b",
                "organization_id": str(other),
            }
        )


@pytest.mark.asyncio
async def test_gmail_dry_run_carries_org_id(sf, monkeypatch):
    # Host .env may enable real Gmail send; force DRY-RUN for this unit test.
    monkeypatch.setenv("GMAIL_SEND_ENABLED", "false")
    get_settings.cache_clear()
    org = uuid.uuid4()
    tool = SendGmailReplyTool(session_factory=sf)
    tool.bind_organization(org)
    result = await tool.run({"to_email": "a@b.com", "subject": "s", "body": "b"})
    data = json.loads(result)
    assert data["mode"] == "DRY_RUN"
    assert data["organization_id"] == str(org)
    monkeypatch.delenv("GMAIL_SEND_ENABLED", raising=False)
    get_settings.cache_clear()


# --- Recipient allowlist (send path) is org-scoped ---------------------------


@pytest.mark.asyncio
async def test_send_rejects_recipient_not_in_org(sf, monkeypatch):
    """Recipient must be a customer of the bound org (or on its allowlist)."""
    org = uuid.uuid4()
    other = uuid.uuid4()
    # put the recipient in the OTHER org only
    async with sf() as s:
        s.add(Customer(id=uuid.uuid4(), organization_id=other, email="x@y.com", name="X"))
        await s.commit()

    settings = get_settings()
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    settings.email_recipient_allowlist = []  # force customer-lookup path
    get_settings.cache_clear()

    tool = SendEmailReplyTool(session_factory=sf)
    tool.bind_organization(org)
    with pytest.raises(ToolExecutionError, match="not allowlisted"):
        await tool.run({"to_email": "x@y.com", "subject": "s", "body_text": "b"})

    monkeypatch.delenv("EMAIL_SEND_ENABLED", raising=False)
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_SMTP_PORT", raising=False)
    get_settings.cache_clear()


# --- Lookup customer is org-scoped even on list cross-tenant -----------------


@pytest.mark.asyncio
async def test_lookup_list_never_leaks_other_org(sf):
    org = uuid.uuid4()
    other = uuid.uuid4()
    async with sf() as s:
        s.add_all([
            Customer(id=uuid.uuid4(), organization_id=org, email="a@a.com", name="A"),
            Customer(id=uuid.uuid4(), organization_id=other, email="b@b.com", name="B"),
        ])
        await s.commit()
    tool = LookupCustomerTool(session_factory=sf)
    tool.bind_organization(org)
    result = await tool.run({"operation": "list"})
    data = json.loads(result)
    assert data["count"] == 1
    assert data["customers"][0]["email"] == "a@a.com"
