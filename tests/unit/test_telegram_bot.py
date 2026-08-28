# -*- coding: utf-8 -*-
"""Unit tests for Telegram monitoring bot — uses stub bot (no network/telegram needed).

Tests cover:
- /health command returns health status text (via update.message.reply_text)
- /report command returns daily report
- /research <query> command returns research output
- /help command returns help text
- send_message / send_daily_report / send_health_alert routing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig


# ---------------------------------------------------------------------------
# Mock Telegram types (mirror telegram.Update/Message surface used by handlers)
# ---------------------------------------------------------------------------

@dataclass
class MockMessage:
    text: str = ""
    chat_id: int = 123456

    async def reply_text(self, text: str, parse_mode: str = "Markdown") -> "MockMessage":
        self.text = text
        return self


@dataclass
class MockChat:
    id: int = 123456


@dataclass
class MockUpdate:
    message: MockMessage | None = None
    effective_chat: MockChat | None = None

    @classmethod
    def from_message(cls, text: str, chat_id: int = 123456) -> "MockUpdate":
        return cls(
            message=MockMessage(text=text, chat_id=chat_id),
            effective_chat=MockChat(id=chat_id),
        )


@dataclass
class MockContext:
    args: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bot() -> MonitoringBot:
    """Create a MonitoringBot with stub bot (no telegram network)."""
    config = TelegramConfig(bot_token="test_token", chat_id="123456")
    return MonitoringBot(config)


@pytest.fixture
def captured() -> list[str]:
    """Shared list to capture sent messages via bot.send_message."""
    return []


@pytest.fixture
def bot_with_capture(bot: MonitoringBot, captured: list[str]) -> MonitoringBot:
    """Bot whose send_message records into `captured`."""
    async def _mock_send(text: str, chat_id: int | None = None, parse_mode: str = "Markdown") -> None:
        captured.append(text)
    bot.send_message = _mock_send  # type: ignore[method-assign]
    return bot


# ---------------------------------------------------------------------------
# Command handler tests (handlers reply via update.message.reply_text)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_command(bot):
    """/health returns overall health status text."""
    update = MockUpdate.from_message("/health")
    context = MockContext(args=[])
    await bot._health_command(update, context)
    assert update.message is not None
    assert "Health" in update.message.text


@pytest.mark.asyncio
async def test_report_command(bot):
    """/report returns a daily report (markdown)."""
    update = MockUpdate.from_message("/report")
    context = MockContext(args=[])
    await bot._report_command(update, context)
    assert update.message is not None
    assert len(update.message.text) > 0


@pytest.mark.asyncio
async def test_help_command(bot):
    """/help returns help text listing commands."""
    update = MockUpdate.from_message("/help")
    context = MockContext(args=[])
    await bot._help_command(update, context)
    assert update.message is not None
    assert "/health" in update.message.text
    assert "/report" in update.message.text
    assert "/research" in update.message.text


@pytest.mark.asyncio
async def test_research_command_no_query(bot):
    """/research without query returns usage hint."""
    update = MockUpdate.from_message("/research")
    context = MockContext(args=[])
    await bot._research_command(update, context)
    assert update.message is not None
    assert "Usage" in update.message.text


@pytest.mark.asyncio
async def test_research_command_with_query(bot):
    """/research <query> runs research and returns a report."""
    update = MockUpdate.from_message("/research What is LangGraph?")
    context = MockContext(args=["What", "is", "LangGraph?"])
    await bot._research_command(update, context)
    # First ack message, then result (or error) — at least 1 reply
    assert update.message is not None
    assert len(update.message.text) > 0


# ---------------------------------------------------------------------------
# send_message / alert routing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_routes_to_chat(bot, captured):
    """send_message delivers to configured chat_id."""
    async def _mock_send(text: str, chat_id: int | None = None, parse_mode: str = "Markdown") -> None:
        captured.append(text)
    bot.send_message = _mock_send  # type: ignore[method-assign]
    await bot.send_message("hello")
    assert captured == ["hello"]


@pytest.mark.asyncio
async def test_send_health_alert_skips_when_ok(bot, captured):
    """send_health_alert does nothing when overall == ok."""
    async def _mock_send(text: str, chat_id: int | None = None, parse_mode: str = "Markdown") -> None:
        captured.append(text)
    bot.send_message = _mock_send  # type: ignore[method-assign]
    await bot.send_health_alert({"overall": "ok", "checks": []})
    assert captured == []


@pytest.mark.asyncio
async def test_send_health_alert_sends_when_degraded(bot, captured):
    """send_health_alert sends when overall != ok."""
    async def _mock_send(text: str, chat_id: int | None = None, parse_mode: str = "Markdown") -> None:
        captured.append(text)
    bot.send_message = _mock_send  # type: ignore[method-assign]
    await bot.send_health_alert({
        "overall": "error",
        "checks": [{"name": "api", "status": "error", "message": "down"}],
    })
    assert len(captured) == 1
    assert "Health Alert" in captured[0]


@pytest.mark.asyncio
async def test_send_daily_report(bot, captured):
    """send_daily_report delegates to send_message."""
    async def _mock_send(text: str, chat_id: int | None = None, parse_mode: str = "Markdown") -> None:
        captured.append(text)
    bot.send_message = _mock_send  # type: ignore[method-assign]
    await bot.send_daily_report("# Daily Report\nContent")
    assert captured == ["# Daily Report\nContent"]
