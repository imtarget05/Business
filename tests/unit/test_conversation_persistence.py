"""Phase 3 Task 3.2 — conversation persistence (TDD).

Covers:
- create → append → read back in order;
- org scoping: a foreign org id sees nothing and can mutate nothing;
- status transitions on conversations.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.database import models
from packages.database.base import Base
from packages.database.models import ConversationStatus, MessageRole
from packages.database.repositories.conversations import ConversationRepository


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
async def db():
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db()}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                models.Organization.__table__,
                models.Conversation.__table__,
                models.Message.__table__,
            ],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def repo(db) -> ConversationRepository:
    return ConversationRepository(db)


async def test_create_append_read_back_in_order(repo, org_id):
    conv = await repo.create_conversation(org_id, "web", subject="Refund help")
    m1 = await repo.append_message(org_id, conv.id, MessageRole.user, "Hi")
    m2 = await repo.append_message(org_id, conv.id, MessageRole.assistant, "Hello!")
    m3 = await repo.append_message(
        org_id,
        conv.id,
        MessageRole.tool,
        "lookup_order",
        tool_metadata={"tool": "crm.lookup", "args": {"order_id": "A-1"}},
    )

    assert (m1.sequence, m2.sequence, m3.sequence) == (1, 2, 3)

    msgs = await repo.list_messages(org_id, conv.id)
    assert [m.content for m in msgs] == ["Hi", "Hello!", "lookup_order"]
    assert [m.role for m in msgs] == [
        MessageRole.user,
        MessageRole.assistant,
        MessageRole.tool,
    ]
    assert msgs[2].tool_metadata == {"tool": "crm.lookup", "args": {"order_id": "A-1"}}


async def test_org_scoping(repo, db, org_id):
    conv = await repo.create_conversation(org_id, "zalo", subject="private")
    await repo.append_message(org_id, conv.id, MessageRole.user, "secret")

    other_org = uuid.uuid4()

    # Reads are scoped.
    assert await repo.get_conversation(other_org, conv.id) is None
    assert await repo.list_messages(other_org, conv.id) == []

    # Writes are scoped: append and status update are no-ops for foreign orgs.
    assert await repo.append_message(other_org, conv.id, MessageRole.user, "hax") is None
    assert await repo.update_status(other_org, conv.id, ConversationStatus.closed) is None
    # Still only the original message — nothing foreign was written.
    msgs = await repo.list_messages(org_id, conv.id)
    assert [m.content for m in msgs] == ["secret"]
    assert (await repo.get_conversation(org_id, conv.id)).status is not None


async def test_status_transitions(repo):
    conv = await repo.create_conversation(org_id := uuid.uuid4(), "email", "Billing")

    assert conv.status == ConversationStatus.open

    updated = await repo.update_status(org_id, conv.id, ConversationStatus.escalated)
    assert updated.status == ConversationStatus.escalated

    updated = await repo.update_status(org_id, conv.id, ConversationStatus.resolved)
    assert updated.status == ConversationStatus.resolved


async def test_create_defaults_open_and_optional_subject(repo):
    conv = await repo.create_conversation(uuid.uuid4(), "web")
    assert conv.status == ConversationStatus.open
    assert conv.subject is None
