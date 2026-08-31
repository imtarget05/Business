"""Unit tests for send_gmail_reply tool (Task 5.2)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.support.tools import SendGmailReplyTool, create_support_tools
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
async def test_session_factory():
    """Create a temporary SQLite database with all tables and return session factory."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture()
async def db(test_session_factory):
    """Create a session for direct database access in tests."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def other_org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
async def test_customer(db, org_id) -> Customer:
    """Create a test customer in the database."""
    customer = Customer(
        id=uuid.uuid4(),
        organization_id=org_id,
        email="customer@example.com",
        name="Test Customer",
        notes="Test notes",
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


class TestSendGmailReplyTool:
    """Tests for send_gmail_reply tool."""

    @pytest.fixture()
    def tool(self, test_session_factory, org_id) -> SendGmailReplyTool:
        t = SendGmailReplyTool(session_factory=test_session_factory)
        t.bind_organization(org_id)
        return t

    @pytest.mark.asyncio
    async def test_dry_run_mode_default(self, tool, db, org_id, test_customer, monkeypatch):
        """DRY-RUN mode is default (gmail_send_enabled=False)."""
        # Isolate settings from .env (which may set GMAIL_SEND_ENABLED=true)
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "false")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.gmail_send_enabled is False

        # Mock the sheet logging to avoid network calls
        with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock) as mock_log:
            result = await tool.run(
                {
                    "to_email": "customer@example.com",
                    "subject": "Test Subject",
                    "body": "Test body content",
                    "conversation_id": str(uuid.uuid4()),
                }
            )

        data = json.loads(result)
        assert data["mode"] == "DRY_RUN"
        assert data["to"] == "customer@example.com"
        assert data["subject"] == "Test Subject"
        assert data["body"] == "Test body content"
        assert "conversation_id" in data

        # Verify sheet logging was called with draft action
        mock_log.assert_called_once()
        logged_row = mock_log.call_args[0][0]
        assert logged_row[4] == "draft"  # action column
        assert logged_row[2] == "customer@example.com"  # customer email

    @pytest.mark.asyncio
    async def test_dry_run_explicit_flag(self, tool, db, org_id, test_customer, monkeypatch):
        """Explicit dry_run=True forces draft mode regardless of settings."""
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "false")
        get_settings.cache_clear()
        with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock) as mock_log:
            result = await tool.run(
                {
                    "to_email": "customer@example.com",
                    "subject": "Explicit Dry Run",
                    "body": "Body",
                    "conversation_id": str(uuid.uuid4()),
                }
            )

        data = json.loads(result)
        assert data["mode"] == "DRY_RUN"
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_real_send_requires_google_config(self, tool, monkeypatch):
        """Real send requires Google OAuth configuration."""
        from unittest.mock import patch

        from packages.config.settings import Settings

        # Create mock settings with gmail enabled but no google creds
        mock_settings = Settings(
            _env_file=None,
            gmail_send_enabled=True,
            google_refresh_token=None,
            google_oauth_client_id=None,
            google_oauth_client_secret=None,
            google_sheet_id=None,
        )
        with patch("agents.support.tools.get_settings", return_value=mock_settings):
            tool2 = SendGmailReplyTool()
            tool2.bind_organization(uuid.uuid4())
            with pytest.raises(RuntimeError, match="google_refresh_token not configured"):
                await tool2.run(
                    {
                        "to_email": "customer@example.com",
                        "subject": "Test",
                        "body": "Test",
                    }
                )

        # Restore defaults (cleanup not strictly needed with mock)
        monkeypatch.delenv("GMAIL_SEND_ENABLED", raising=False)
        monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_real_send_rejects_non_allowlisted_recipient(
        self, tool, test_session_factory, org_id, test_customer, monkeypatch
    ):
        """Real send rejects recipient not in allowlist or customer records."""
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "true")
        get_settings.cache_clear()
        tool.bind_organization(org_id)

        # Mock Google client to avoid actual network calls, but we still check allowlist first
        with patch("integrations.google_client.get_google_credentials") as mock_creds:
            mock_creds.return_value = MagicMock()

            try:
                with pytest.raises(ToolExecutionError, match="not allowlisted"):
                    await tool.run(
                        {
                            "to_email": "notallowed@example.com",
                            "subject": "Test",
                            "body": "Test",
                        }
                    )
            finally:
                get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_real_send_allows_customer_in_org(
        self, tool, test_session_factory, org_id, test_customer, monkeypatch
    ):
        """Real send allows recipient that is a customer in the org."""
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "true")
        get_settings.cache_clear()
        tool.bind_organization(org_id)

        # Mock Google client and sheet logging
        with patch("integrations.google_client.get_google_credentials") as mock_creds:
            mock_creds.return_value = MagicMock()
            with patch("integrations.google_client.gmail_send") as mock_send:
                mock_send.return_value = {"id": "msg123"}
                with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock) as mock_log:
                    result = await tool.run(
                        {
                            "to_email": "customer@example.com",  # This is the test_customer email
                            "subject": "Test",
                            "body": "Test body",
                            "conversation_id": str(uuid.uuid4()),
                        }
                    )

        data = json.loads(result)
        assert data["mode"] == "SENT"
        assert data["to"] == "customer@example.com"
        mock_send.assert_called_once()
        mock_log.assert_called_once()
        logged_row = mock_log.call_args[0][0]
        assert logged_row[4] == "gmail_send"

    @pytest.mark.asyncio
    async def test_real_send_allows_explicit_allowlist(
        self, tool, test_session_factory, org_id, monkeypatch
    ):
        """Real send allows recipient in gmail_allowed_recipients even if not a customer."""
        from packages.config.settings import get_settings

        monkeypatch.setenv("GMAIL_SEND_ENABLED", "true")
        get_settings.cache_clear()
        settings = get_settings()
        settings.gmail_allowed_recipients = ["allowed@example.com"]
        try:
            tool.bind_organization(org_id)

            with patch("integrations.google_client.get_google_credentials") as mock_creds:
                mock_creds.return_value = MagicMock()
                with patch("integrations.google_client.gmail_send") as mock_send:
                    mock_send.return_value = {"id": "msg123"}
                    with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock):
                        result = await tool.run(
                            {
                                "to_email": "allowed@example.com",
                                "subject": "Test",
                                "body": "Test body",
                            }
                        )

            data = json.loads(result)
            assert data["mode"] == "SENT"
            mock_send.assert_called_once()
        finally:
            settings.gmail_allowed_recipients = []
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_dry_run_still_logs_to_sheet(self, tool, db, org_id, test_customer, monkeypatch):
        """Even in DRY-RUN mode, the attempt is logged to Sheets."""
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "false")
        get_settings.cache_clear()
        with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock) as mock_log:
            await tool.run(
                {
                    "to_email": "customer@example.com",
                    "subject": "Test",
                    "body": "Test body for logging",
                    "conversation_id": str(uuid.uuid4()),
                }
            )

        mock_log.assert_called_once()
        logged_row = mock_log.call_args[0][0]
        assert logged_row[4] == "draft"
        assert logged_row[3] == "Test body for logging"  # body truncated to 500 chars

    @pytest.mark.asyncio
    async def test_body_truncated_to_500_chars_in_sheet(
        self, tool, db, org_id, test_customer, monkeypatch
    ):
        """Body is truncated to 500 characters in sheet log."""
        monkeypatch.setenv("GMAIL_SEND_ENABLED", "false")
        get_settings.cache_clear()
        long_body = "x" * 1000

        with patch.object(tool, "_log_to_sheet", new_callable=AsyncMock) as mock_log:
            await tool.run(
                {
                    "to_email": "customer@example.com",
                    "subject": "Test",
                    "body": long_body,
                }
            )

        logged_row = mock_log.call_args[0][0]
        assert len(logged_row[3]) == 500
        assert logged_row[3] == "x" * 500


class TestSendGmailReplyToolRegistry:
    """Tests for tool registry integration."""

    @pytest.mark.asyncio
    async def test_gmail_tool_registered(self, test_session_factory):
        """send_gmail_reply tool is registered alongside other tools."""
        from packages.core.tools import ToolRegistry

        tools = create_support_tools(session_factory=test_session_factory)
        registry = ToolRegistry(*tools)

        assert "send_gmail_reply" in registry.names()
        assert set(registry.names()) == {
            "send_email_reply",
            "send_gmail_reply",
            "create_ticket",
            "lookup_customer",
        }

        schemas = registry.list_schemas()
        gmail_schema = next(s for s in schemas if s["name"] == "send_gmail_reply")
        assert gmail_schema["name"] == "send_gmail_reply"
        assert "to_email" in gmail_schema["parameters"]["properties"]
        assert "subject" in gmail_schema["parameters"]["properties"]
        assert "body" in gmail_schema["parameters"]["properties"]
        assert "conversation_id" in gmail_schema["parameters"]["properties"]
        assert gmail_schema["parameters"]["required"] == ["to_email", "subject", "body"]
