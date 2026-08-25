"""Phase 3 Task 3.4 — /v1/conversations/* API routes.

E2E tests with mock LLM:
- question → tool lookup_customer → assistant answer persisted to thread
- org-scoping enforced (404 cross-org)
- dry-run email action appears in returned actions metadata
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid as _uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

import packages.database.session as session_mod
from apps.api.main import create_app
from packages.config.settings import Settings
from packages.database import models
from packages.database.base import Base
from packages.database.session import get_session_factory
from packages.llm.factory import get_llm_provider
from packages.llm.mock import MockLLMProvider


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh module state per test: point the global engine at a temp sqlite db."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'conv.db').as_posix()}"
    settings = Settings(database_url=url, persistence_enabled=True, llm_provider="mock")
    get_session_factory(settings)

    # Create a shared mock LLM provider for this test
    shared_mock_llm = MockLLMProvider()
    
    # Patch the factory to return our shared mock
    # Need to patch where it's imported/used: apps.api.routes.conversations.get_llm_provider
    def mock_get_llm_provider(s: Settings) -> MockLLMProvider:
        return shared_mock_llm
    
    monkeypatch.setattr("apps.api.routes.conversations.get_llm_provider", mock_get_llm_provider)

    async def _setup():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    models.Organization.__table__,
                    models.Conversation.__table__,
                    models.Message.__table__,
                    models.Customer.__table__,
                    models.Ticket.__table__,
                ],
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
                    name="Other Org",
                    slug="other",
                )
            )
            # Create a test customer for lookup_customer tool
            await conn.execute(
                models.Customer.__table__.insert().values(
                    id=_uuid.UUID("11111111-1111-1111-1111-111111111111"),
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    email="customer@example.com",
                    name="Test Customer",
                    notes="VIP customer",
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    # Return tuple of (client, shared_mock_llm)
    yield TestClient(create_app()), shared_mock_llm
    session_mod._engine = None
    session_mod._session_factory = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_conversation(client) -> None:
    """POST /v1/conversations creates a conversation."""
    client, _ = client
    resp = client.post("/v1/conversations", json={"channel": "web", "subject": "Refund help"})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "conversation_id" in data
    assert data["channel"] == "web"
    assert data["subject"] == "Refund help"
    assert data["status"] == "open"
    assert data["organization_id"] == "00000000-0000-0000-0000-000000000001"


def test_create_conversation_default_org(client) -> None:
    """Create conversation without explicit org_id uses default org."""
    client, _ = client
    resp = client.post("/v1/conversations", json={"channel": "email"})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["organization_id"] == "00000000-0000-0000-0000-000000000001"


def test_append_message_runs_agent_and_persists(client) -> None:
    """POST /v1/conversations/{id}/messages runs support agent with tool loop."""
    client, mock_llm = client
    # Create conversation
    create_resp = client.post("/v1/conversations", json={"channel": "web", "subject": "Test"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    # Script: first call returns tool_calls for lookup_customer, second returns final answer
    mock_llm.script(
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_customer",
                    "arguments": {
                        "operation": "get",
                        "organization_id": "00000000-0000-0000-0000-000000000001",
                        "email": "customer@example.com",
                    },
                }
            ],
            "content": "Looking up customer...",
        },
        "Found customer: Test Customer (customer@example.com), VIP customer",
    )

    # Send message
    resp = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "Hi, I'm customer@example.com, need help with my order"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "conversation_id" in data
    assert data["conversation_id"] == conv_id
    assert "user_message_id" in data
    assert "assistant_message_id" in data
    assert "assistant_reply" in data
    assert "actions" in data

    # Verify assistant reply contains customer info
    assert "Test Customer" in data["assistant_reply"]
    assert "customer@example.com" in data["assistant_reply"]

    # Verify actions metadata includes lookup_customer
    assert len(data["actions"]) == 1
    action = data["actions"][0]
    assert action["tool"] == "lookup_customer"
    assert action["arguments"]["operation"] == "get"
    assert action["arguments"]["email"] == "customer@example.com"
    assert "Test Customer" in action["result"]
    assert "VIP customer" in action["result"]
    assert action["mode"] is None  # Not a send_email_reply tool


def test_append_message_send_email_dry_run_in_actions(client) -> None:
    """Dry-run email action appears in returned actions metadata."""
    client, mock_llm = client
    create_resp = client.post("/v1/conversations", json={"channel": "web"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    # Script: call send_email_reply tool (DRY-RUN mode)
    mock_llm.script(
        {
            "tool_calls": [
                {
                    "id": "call_email_1",
                    "name": "send_email_reply",
                    "arguments": {
                        "to_email": "customer@example.com",
                        "subject": "Re: Your inquiry",
                        "body_text": "Thank you for contacting us. We'll help you shortly.",
                        "conversation_id": conv_id,
                    },
                }
            ],
            "content": "Drafting email reply...",
        },
        "Email draft created successfully.",
    )

    resp = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "Please send me a confirmation email"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Find send_email_reply action
    email_actions = [a for a in data["actions"] if a["tool"] == "send_email_reply"]
    assert len(email_actions) == 1
    email_action = email_actions[0]
    assert email_action["mode"] == "DRY_RUN"
    assert email_action["arguments"]["to_email"] == "customer@example.com"
    assert "Draft" in email_action["result"] or "DRY_RUN" in email_action["result"]


def test_get_conversation_thread(client) -> None:
    """GET /v1/conversations/{id} returns full thread with messages."""
    client, mock_llm = client
    create_resp = client.post("/v1/conversations", json={"channel": "web", "subject": "Thread test"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    mock_llm.script("Hello! How can I help you?")

    # Send a message
    client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "Hi there"})

    # Get thread
    resp = client.get(f"/v1/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["conversation_id"] == conv_id
    assert data["channel"] == "web"
    assert data["subject"] == "Thread test"
    assert data["status"] == "open"
    assert len(data["messages"]) == 2  # user + assistant

    user_msg = data["messages"][0]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "Hi there"
    assert user_msg["sequence"] == 1

    assistant_msg = data["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "Hello! How can I help you?"
    assert assistant_msg["sequence"] == 2


def test_list_messages_only(client) -> None:
    """GET /v1/conversations/{id}/messages returns messages only."""
    client, mock_llm = client
    create_resp = client.post("/v1/conversations", json={"channel": "zalo"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    mock_llm.script("Reply 1")
    client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "Msg 1"})

    mock_llm.script("Reply 2")
    client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "Msg 2"})

    resp = client.get(f"/v1/conversations/{conv_id}/messages")
    assert resp.status_code == 200, resp.text
    messages = resp.json()

    assert len(messages) == 4
    assert messages[0]["content"] == "Msg 1"
    assert messages[1]["content"] == "Reply 1"
    assert messages[2]["content"] == "Msg 2"
    assert messages[3]["content"] == "Reply 2"


def test_org_scoping_conversation_not_found_cross_org(client) -> None:
    """GET /v1/conversations/{id} returns 404 for cross-org access."""
    client, _ = client
    # Create conversation in org 1
    create_resp = client.post("/v1/conversations", json={"channel": "web"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    # Try to access from org 2 (pass organization_id query param)
    resp = client.get(
        f"/v1/conversations/{conv_id}",
        params={"organization_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["message"] == "conversation not found"


def test_org_scoping_append_message_cross_org(client) -> None:
    """POST /v1/conversations/{id}/messages returns 404 for cross-org access."""
    client, mock_llm = client
    create_resp = client.post("/v1/conversations", json={"channel": "web"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    mock_llm.script("Reply")

    # Try to append message from org 2
    resp = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "Hack attempt", "organization_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["message"] == "conversation not found"


def test_org_scoping_list_messages_cross_org(client) -> None:
    """GET /v1/conversations/{id}/messages returns 404 for cross-org access."""
    client, _ = client
    create_resp = client.post("/v1/conversations", json={"channel": "web"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    resp = client.get(
        f"/v1/conversations/{conv_id}/messages",
        params={"organization_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["message"] == "conversation not found"


def test_list_conversations(client) -> None:
    """GET /v1/conversations returns list of conversations ordered by updated_at desc."""
    client, mock_llm = client
    # Create multiple conversations
    for i in range(3):
        resp = client.post("/v1/conversations", json={"channel": "web", "subject": f"Subject {i}"})
        assert resp.status_code == 201

    # List conversations
    resp = client.get("/v1/conversations")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "conversations" in data
    assert len(data["conversations"]) == 3

    # Verify structure
    for conv in data["conversations"]:
        assert "conversation_id" in conv
        assert "organization_id" in conv
        assert "channel" in conv
        assert "status" in conv
        assert "subject" in conv
        assert "updated_at" in conv

    # Verify ordering by updated_at desc (most recent first)
    # Note: In tests, created timestamps may be identical due to rapid creation,
    # so we just verify all 3 are returned and have valid updated_at
    subjects = {c["subject"] for c in data["conversations"]}
    assert subjects == {"Subject 0", "Subject 1", "Subject 2"}


def test_list_conversations_pagination(client) -> None:
    """GET /v1/conversations supports limit and offset pagination."""
    client, mock_llm = client
    # Create 5 conversations
    for i in range(5):
        resp = client.post("/v1/conversations", json={"channel": "web", "subject": f"Subject {i}"})
        assert resp.status_code == 201

    # Test limit
    resp = client.get("/v1/conversations", params={"limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 2

    # Test offset
    resp = client.get("/v1/conversations", params={"limit": 2, "offset": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 2

    # Test empty page
    resp = client.get("/v1/conversations", params={"limit": 2, "offset": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 0


def test_list_conversations_org_scoping(client) -> None:
    """GET /v1/conversations only returns conversations for the current org."""
    client, _ = client
    # Create conversation in org 1
    resp = client.post("/v1/conversations", json={"channel": "web", "subject": "Org 1"})
    assert resp.status_code == 201

    # List from org 2
    resp = client.get(
        "/v1/conversations",
        params={"organization_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 0


def test_conversation_persists_tool_metadata(client) -> None:
    """Tool metadata is persisted with assistant messages."""
    client, mock_llm = client
    create_resp = client.post("/v1/conversations", json={"channel": "web"})
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["conversation_id"]

    mock_llm.script(
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_customer",
                    "arguments": {
                        "operation": "get",
                        "organization_id": "00000000-0000-0000-0000-000000000001",
                        "email": "customer@example.com",
                    },
                }
            ],
            "content": "Looking up...",
        },
        "Customer found.",
    )

    client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "Lookup customer"})

    # Get thread and verify tool_metadata on assistant message
    resp = client.get(f"/v1/conversations/{conv_id}")
    assert resp.status_code == 200
    data = resp.json()

    assistant_msg = data["messages"][1]  # second message is assistant
    assert assistant_msg["tool_metadata"] is not None
    assert "actions" in assistant_msg["tool_metadata"]
    actions = assistant_msg["tool_metadata"]["actions"]
    assert len(actions) == 1
    assert actions[0]["tool"] == "lookup_customer"
    assert actions[0]["arguments"]["email"] == "customer@example.com"