"""Adversarial tests for Advisory Council persona routing + Sales intent detection.

Covers both the pure persona selector and the free-text routing inside the
Telegram handler (which must NOT hijack jobsearch/research into advisory/sales).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pytest

sys.path.insert(0, ".")

from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig
from packages.core.personas import select_persona

# --- pure persona selector ----------------------------------------------------


def test_buffett_detected():
    # avoid hormozi keywords (e.g. 'giá' in 'giá trị') so buffett wins on order
    assert select_persona("warren buffett đầu tư chứng khoán dài hạn") == "buffett"


def test_hormozi_detected():
    assert select_persona("chiến lược tăng trưởng doanh thu") == "hormozi"


def test_garyvee_detected():
    assert select_persona("marketing cho startup trên tiktok") == "garyvee"


def test_none_for_generic_question():
    assert select_persona("nghề nào layoff nhiều 2026?") is None


def test_none_for_empty():
    assert select_persona("") is None


def test_first_match_wins_order():
    # 'growth' is hormozi, 'invest' is buffett; a text with both -> hormozi (checked first)
    assert select_persona("tăng trưởng và đầu tư") == "hormozi"


def test_persona_keyword_not_substring_false_positive():
    # 'chứng' alone is not a keyword; 'chứng khoán' is. 'chứng chỉ' must NOT route to buffett.
    assert select_persona("làm chứng chỉ kế toán") is None


def test_vietnamese_invest_keyword():
    assert select_persona("cổ phiếu nào nên mua") == "buffett"


# --- Telegram free-text routing (advisory/sales must not steal other intents) --


@dataclass
class MockMessage:
    text: str = ""
    chat_id: int = 123456

    async def reply_text(
        self, text: str, parse_mode: str = "Markdown", reply_markup=None
    ) -> MockMessage:
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
    def from_message(cls, text: str, chat_id: int = 123456) -> MockUpdate:
        return cls(
            message=MockMessage(text=text, chat_id=chat_id), effective_chat=MockChat(id=chat_id)
        )


@dataclass
class MockBot:
    sent: list = field(default_factory=list)

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup=None
    ) -> None:
        self.sent.append(text)

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        pass


@dataclass
class MockContext:
    args: list[str] = field(default_factory=list)
    bot: MockBot = field(default_factory=MockBot)


@pytest.fixture
def bot() -> MonitoringBot:
    return MonitoringBot(TelegramConfig(bot_token="t", chat_id="123456"))


@pytest.fixture
def ctx() -> MockContext:
    return MockContext()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    import packages.core.bootstrap as bootstrap

    async def _noop_advisory(self, update, context):
        await update.message.reply_text("[advisory-routed]")

    async def _noop_sales(self, update, context):
        await update.message.reply_text("[sales-routed]")

    monkeypatch.setattr(MonitoringBot, "_advisory_command", _noop_advisory)
    monkeypatch.setattr(MonitoringBot, "_sales_command", _noop_sales)

    # Keep get_container raising so the generic fallback does not hit a real LLM.
    async def _boom(*a, **k):
        raise RuntimeError("isolated")

    monkeypatch.setattr(bootstrap, "get_container", _boom)
    yield


async def _run(bot, ctx, text, chat_id=123456):
    update = MockUpdate.from_message(text, chat_id=chat_id)
    ctx.bot = MockBot()
    await bot._message_handler(update, ctx)
    return update


@pytest.mark.asyncio
async def test_jobsearch_not_routed_to_advisory(bot, ctx):
    update = await _run(bot, ctx, "tìm 5 job AI intern về a@b.com")
    assert "[advisory-routed]" not in (update.message.text or "")
    # JobSearch confirm screen must appear (not an advisory routing) — using the
    # post-UX-review wording ("Xác nhận tìm kiếm việc làm").
    assert "Xác nhận tìm kiếm việc làm" in (update.message.text or "")


@pytest.mark.asyncio
async def test_research_not_routed_to_advisory(bot, ctx):
    update = await _run(bot, ctx, "tìm hiểu thị trường việc làm Việt Nam")
    assert "[advisory-routed]" not in (update.message.text or "")


@pytest.mark.asyncio
async def test_sales_intent_routed(bot, ctx):
    update = await _run(bot, ctx, "viết proposal báo giá cho khách hàng ABC")
    assert "[sales-routed]" in (update.message.text or "")


@pytest.mark.asyncio
async def test_advisory_persona_routed(bot, ctx):
    update = await _run(bot, ctx, "hỏi Hormozi về chiến lược tăng trưởng")
    assert "[advisory-routed]" in (update.message.text or "")
