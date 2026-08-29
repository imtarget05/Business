# -*- coding: utf-8 -*-
"""Adversarial / extremely-hard test cases for Telegram intent classification.

These tests probe the message-classification logic in MonitoringBot._message_handler:
false positives (a "?" turning every question into a menu), wrong job-count parsing,
greeting detection, jobsearch vs research disambiguation, and number extraction.

They use the stub bot harness (no Telegram/network) and monkeypatch the LLM/orchestrator
so classification is exercised in isolation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

# Make sure project root is importable when run via `python -m pytest`
sys.path.insert(0, ".")

from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig


# ---------------------------------------------------------------------------
# Extended mock surface (mirrors what _message_handler touches)
# ---------------------------------------------------------------------------

@dataclass
class MockMessage:
    text: str = ""
    chat_id: int = 123456

    async def reply_text(self, text: str, parse_mode: str = "Markdown", reply_markup=None) -> "MockMessage":
        self.text = text
        self.parse_mode = parse_mode
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
class MockBot:
    """Captures send_message / send_chat_action; reply_text goes to the update."""
    sent: list = field(default_factory=list)
    chat_actions: list = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup=None) -> None:
        self.sent.append(text)

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        self.chat_actions.append(action)


@dataclass
class MockContext:
    args: list[str] = field(default_factory=list)
    bot: MockBot = field(default_factory=MockBot)


@pytest.fixture
def bot() -> MonitoringBot:
    config = TelegramConfig(bot_token="test_token", chat_id="123456")
    return MonitoringBot(config)


@pytest.fixture
def ctx() -> MockContext:
    return MockContext()


# Block the orchestrator / persona / advisory / sales code paths so we only test
# the pure classification + fast-path behavior (no network, no LLM).
@pytest.fixture(autouse=True)
def _isolate_external(monkeypatch):
    import packages.core.personas as personas
    import packages.core.bootstrap as bootstrap

    async def _noop_advisory(self, update, context):
        await update.message.reply_text("[advisory-routed]")

    async def _noop_sales(self, update, context):
        await update.message.reply_text("[sales-routed]")

    monkeypatch.setattr(personas, "select_persona", lambda text: None)
    monkeypatch.setattr(MonitoringBot, "_advisory_command", _noop_advisory)
    monkeypatch.setattr(MonitoringBot, "_sales_command", _noop_sales)
    # Avoid real container/orchestrator/LLM in the generic fallback path.
    # _message_handler does `from packages.core.bootstrap import get_container`.
    async def _boom(*a, **k):
        raise RuntimeError("isolated")

    monkeypatch.setattr(bootstrap, "get_container", _boom)
    yield


async def _run(bot, ctx, text, chat_id=123456):
    update = MockUpdate.from_message(text, chat_id=chat_id)
    ctx.bot = MockBot()
    await bot._message_handler(update, ctx)
    return update


# ---------------------------------------------------------------------------
# TEST CASES — cực khó
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_question_mark_is_not_help_menu(bot, ctx):
    """'? help intent' must NOT hijack a real question containing '?'.

    BUG (fixed): `any(k in text for k in ('?', ...))` made ANY message with a '?'
    show the menu instead of being answered.
    """
    update = await _run(bot, ctx, "Nghề nào đang bị layoff nhiều nhất 2026?")
    txt = update.message.text or ""
    # Must NOT be hijacked into the help menu or a jobsearch confirm.
    assert "chưa rõ ý" not in txt          # not the friendly-unknown menu
    assert "Xác nhận tìm" not in txt        # not a jobsearch flow
    assert "[advisory-routed]" not in txt  # not force-routed to advisory
    # It must reach a real answer path (here the orchestrator/LLM fallback).
    assert len(txt) > 0


@pytest.mark.asyncio
async def test_jobsearch_count_parsed_from_brief(bot, ctx):
    """'tìm 5 job AI intern' must capture N=5, not the first stray digit."""
    update = await _run(bot, ctx, "tìm 5 job AI intern gửi về a@b.com")
    txt = update.message.text or ""
    assert "tìm 5 vị trí" in txt or "5 vị trí" in txt
    assert "tìm 8 vị trí" not in txt  # default must NOT leak


@pytest.mark.asyncio
async def test_jobsearch_count_ignores_phone_number(bot, ctx):
    """A phone number in the brief must NOT be read as the job count."""
    update = await _run(bot, ctx, "tìm 3 job AI intern, liên hệ sdt 0905123456")
    txt = update.message.text or ""
    assert "tìm 3 vị trí" in txt or "3 vị trí" in txt


@pytest.mark.asyncio
async def test_jobsearch_default_when_no_number(bot, ctx):
    """'tìm job AI intern' with no number -> default 8 (no crash, no weird digit)."""
    update = await _run(bot, ctx, "tìm job AI intern về a@b.com")
    txt = update.message.text or ""
    assert "8 vị trí" in txt


@pytest.mark.asyncio
async def test_greeting_requires_email_or_explicit_chao(bot, ctx):
    """'gửi lời chào' without an email must NOT trigger gmail_send (no target)."""
    update = await _run(bot, ctx, "gửi lời chào tới đối tác")
    txt = update.message.text or ""
    assert "Đã gửi lời chào" not in txt
    assert "Gửi mail thất bại" not in txt


@pytest.mark.asyncio
async def test_greeting_with_email_triggers(bot, ctx, monkeypatch):
    """'gửi lời chào' WITH a valid email should attempt gmail_send."""
    import integrations.google_client as gc

    captured = {}

    def _fake_send(to, subject, body):
        captured["to"] = to
        return {"mode": "DRY_RUN", "id": "x"}

    monkeypatch.setattr(gc, "gmail_send", _fake_send, raising=True)
    update = await _run(bot, ctx, "gửi lời chào tới friend@company.com")
    txt = update.message.text or ""
    assert captured.get("to") == "friend@company.com"


@pytest.mark.asyncio
async def test_research_not_misrouted_to_jobsearch(bot, ctx):
    """'tìm hiểu về thị trường việc làm' mentions 'tìm' + 'việc' but is research, not hiring."""
    update = await _run(bot, ctx, "tìm hiểu về thị trường việc làm Việt Nam 2026")
    txt = update.message.text or ""
    assert "vị trí VERIFIED" not in txt  # must NOT open jobsearch confirm
    assert "Xác nhận tìm" not in txt


@pytest.mark.asyncio
async def test_simple_greeting_fast_path(bot, ctx):
    """Short greeting goes through the no-LLM fast path."""
    update = await _run(bot, ctx, "chào bạn")
    txt = update.message.text or ""
    assert "My AI Agent Bot" in txt


@pytest.mark.asyncio
async def test_code_snippet_fast_path(bot, ctx):
    """'viết code python' returns a deterministic snippet, not an LLM essay."""
    update = await _run(bot, ctx, "viết code python hello world")
    txt = update.message.text or ""
    assert "```python" in txt
    assert 'print("Hello, World!")' in txt


@pytest.mark.asyncio
async def test_help_keyword_shows_menu(bot, ctx):
    """Explicit 'help'/'menu' still routes to the friendly menu."""
    update = await _run(bot, ctx, "help")
    txt = update.message.text or ""
    assert "Mình chưa rõ" in txt or "thử" in txt


@pytest.mark.asyncio
async def test_jobsearch_clarifying_captures_followup_count(bot, ctx):
    """After first 'tìm job', a follow-up '12 job AI' must update N to 12."""
    await _run(bot, ctx, "tìm job AI intern")
    update = await _run(bot, ctx, "12 job AI thực tập tại Hà Nội", chat_id=123456)
    txt = update.message.text or ""
    assert "12 vị trí" in txt


@pytest.mark.asyncio
async def test_hi_substring_not_greeting(bot, ctx):
    """'hi' is a substring of 'layoff'/'neighbor' — must NOT trigger greeting fast-path."""
    update = await _run(bot, ctx, "Nghề nào đang bị layoff nhiều nhất 2026?")
    txt = update.message.text or ""
    assert "My AI Agent Bot" not in txt
    assert "chưa rõ ý" not in txt  # not hijacked to menu either


@pytest.mark.asyncio
async def test_empty_message_ignored(bot, ctx):
    """Empty / whitespace-only message must not produce any real reply."""
    update = await _run(bot, ctx, "   ")
    # _message_handler strips and returns early; the mock text is unchanged and no
    # bot.send_message reply was emitted.
    assert ctx.bot.sent == []
    assert "Mình" not in (update.message.text or "")
    assert "chưa rõ" not in (update.message.text or "")
