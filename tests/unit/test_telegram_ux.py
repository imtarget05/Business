# -*- coding: utf-8 -*-
"""Unit tests for Telegram UX improvements (Feature 5) — stub bot, no network.

Covers:
- typing indicator (`send_chat_action`) fired BEFORE the web/LLM lookup
- session context retained across two messages (quick reply -> follow-up query)
- pagination: first page + "Tiếp ▶️" callback returning the next page
- quick-reply keyboard present on /help, on unknown input and routing correctly

Uses the same mock-bot pattern as tests/unit/test_telegram_bot.py, extended with
`send_chat_action`, `reply_markup` capture and a callback-query mock.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

sys.path.insert(0, ".")

import agents.monitoring.telegram_bot as tb
from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig


# ---------------------------------------------------------------------------
# Mock Telegram surface (superset of the one in test_telegram_bot.py)
# ---------------------------------------------------------------------------

@dataclass
class MockMessage:
    text: str = ""
    chat_id: int = 123456
    replies: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)

    async def reply_text(
        self, text: str, parse_mode: str = "Markdown", reply_markup: Any = None
    ) -> "MockMessage":
        self.text = text
        self.replies.append(text)
        self.markups.append(reply_markup)
        return self


@dataclass
class MockChat:
    id: int = 123456


@dataclass
class MockUser:
    id: int = 777
    language_code: str = "vi"


@dataclass
class MockUpdate:
    message: MockMessage | None = None
    effective_chat: MockChat | None = None
    effective_user: MockUser | None = None

    @classmethod
    def from_message(
        cls, text: str, chat_id: int = 123456, user_id: int = 777
    ) -> "MockUpdate":
        return cls(
            message=MockMessage(text=text, chat_id=chat_id),
            effective_chat=MockChat(id=chat_id),
            effective_user=MockUser(id=user_id),
        )


@dataclass
class MockBot:
    """Captures send_message / send_chat_action onto a shared event timeline."""

    events: list[tuple[str, Any]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    chat_actions: list[str] = field(default_factory=list)

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup: Any = None
    ) -> None:
        self.sent.append(text)
        self.events.append(("send_message", text))

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        self.chat_actions.append(action)
        self.events.append((action, chat_id))


@dataclass
class MockContext:
    args: list[str] = field(default_factory=list)
    bot: MockBot = field(default_factory=MockBot)


@dataclass
class MockCallbackQuery:
    data: str = ""
    from_user: MockUser = field(default_factory=MockUser)
    message: MockMessage = field(default_factory=MockMessage)
    answered: int = 0
    edited: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)

    async def answer(self) -> None:
        self.answered += 1

    async def edit_message_text(
        self, text: str, parse_mode: str | None = None, reply_markup: Any = None
    ) -> "MockCallbackQuery":
        self.edited.append(text)
        self.markups.append(reply_markup)
        return self


@dataclass
class MockCallbackUpdate:
    callback_query: MockCallbackQuery
    message: MockMessage | None = None
    effective_chat: MockChat | None = None
    effective_user: MockUser | None = None


class _NoopMemory:
    """Replaces ChatMemory so no PostgreSQL is touched."""

    async def log_user(self, *a: Any, **k: Any) -> None:
        return None

    async def log_assistant(self, *a: Any, **k: Any) -> None:
        return None

    async def customer_profile_blurb(self, *a: Any, **k: Any) -> str:
        return ""

    async def recent_history(self, *a: Any, **k: Any) -> list:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bot() -> MonitoringBot:
    b = MonitoringBot(TelegramConfig(bot_token="test_token", chat_id="123456"))
    b._chat_memory = _NoopMemory()  # type: ignore[attr-defined]
    return b


@pytest.fixture
def ctx() -> MockContext:
    return MockContext()


@pytest.fixture(autouse=True)
def _isolate_external(monkeypatch):
    """Keep the handler offline: no persona routing, no container, no LLM."""
    import packages.core.bootstrap as bootstrap
    import packages.core.personas as personas

    monkeypatch.setattr(personas, "select_persona", lambda text: None)

    def _boom(*a: Any, **k: Any):
        # Sync raise: get_container() is called (not awaited) by the handler.
        raise RuntimeError("isolated")

    monkeypatch.setattr(bootstrap, "get_container", _boom)
    yield


def _keyboard_labels(markup: Any) -> list[str]:
    """Flatten a ReplyKeyboardMarkup (real or stub) into button labels."""
    rows = getattr(markup, "keyboard", None) or []
    return [getattr(btn, "text", str(btn)) for row in rows for btn in row]


def _inline_buttons(markup: Any) -> list[tuple[str, str]]:
    """Flatten an InlineKeyboardMarkup into [(label, callback_data)]."""
    rows = getattr(markup, "inline_keyboard", None) or []
    return [
        (getattr(b, "text", ""), getattr(b, "callback_data", ""))
        for row in rows
        for b in row
    ]


def _fake_search(events: list, results: list[dict]):
    """Fake _real_web_search that records its call on the shared timeline."""

    async def _search(query: str) -> list[dict]:
        events.append(("web_search", query))
        return results

    return _search


def _results(n: int) -> list[dict]:
    return [
        {
            "title": f"Quán ăn số {i}",
            "snippet": f"Mô tả quán {i}",
            "url": f"https://guide.michelin.com/vn/quan-{i}",
        }
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# 1. Typing indicator before every LLM/web call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_typing_indicator_sent_before_web_lookup(bot, ctx, monkeypatch):
    """The "đang nhập…" action must reach the user BEFORE the slow web call."""
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(3)))

    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội")
    await bot._message_handler(update, ctx)

    kinds = [name for name, _ in ctx.bot.events]
    assert "typing" in ctx.bot.chat_actions
    assert "web_search" in kinds
    assert kinds.index("typing") < kinds.index("web_search"), kinds


@pytest.mark.asyncio
async def test_typing_helper_prefers_given_bot_and_never_raises(bot):
    """_typing works with the mock bot, and degrades quietly without one."""
    mock = MockBot()
    assert await bot._typing(123456, mock) is True
    assert mock.chat_actions == ["typing"]

    class _NoAction:
        pass

    # No usable bot -> False, no exception (UX must never break a handler).
    bot.bot = _NoAction()  # type: ignore[assignment]
    assert await bot._typing(123456, _NoAction()) is False
    assert await bot._typing(None, mock) is False


@pytest.mark.asyncio
async def test_typing_helper_supports_positional_only_mock(bot):
    """A mock bot whose send_chat_action takes positionals still works."""
    calls: list[tuple[Any, str]] = []

    class _PositionalBot:
        async def send_chat_action(self, chat_id, action):  # noqa: ANN001
            if chat_id is None:
                raise TypeError("chat_id required")
            calls.append((chat_id, action))

    assert await bot._typing(42, _PositionalBot()) is True
    assert calls == [(42, "typing")]


# ---------------------------------------------------------------------------
# 2. Session context across messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_context_retained_across_two_messages(bot, ctx, monkeypatch):
    """Bấm "🔍 Tìm món ăn" rồi gõ món ở lượt sau -> bot vẫn hiểu ngữ cảnh."""
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(3)))

    first = MockUpdate.from_message(tb.QUICK_REPLY_FOOD, user_id=555)
    await bot._message_handler(first, ctx)
    session = bot.sessions.get(555)
    assert session is not None
    assert session.last_capability == tb.AWAITING_FOOD_QUERY
    assert "bạn muốn tìm" in first.message.text.lower()

    second = MockUpdate.from_message("bún bò Huế", user_id=555)
    await bot._message_handler(second, ctx)

    # The follow-up was routed by SESSION context (no food keyword in the text).
    assert ("web_search", "bún bò Huế") in ctx.bot.events
    session = bot.sessions.get(555)
    assert session is not None
    assert session.last_query == "bún bò Huế"
    assert session.history == [tb.QUICK_REPLY_FOOD, "bún bò Huế"]
    assert len(session.results) == 3
    # Marker consumed: the next message is classified normally again.
    assert session.last_capability != tb.AWAITING_FOOD_QUERY


@pytest.mark.asyncio
async def test_session_history_accumulates_per_user(bot, ctx):
    """History is keyed by telegram_user_id, not by chat."""
    await bot._message_handler(MockUpdate.from_message("tôi cần hỗ trợ", user_id=1), ctx)
    await bot._message_handler(MockUpdate.from_message("khiếu nại đơn hàng", user_id=1), ctx)
    await bot._message_handler(MockUpdate.from_message("báo cáo", user_id=2), ctx)

    first = bot.sessions.get(1)
    second = bot.sessions.get(2)
    assert first is not None and second is not None
    assert first.history == ["tôi cần hỗ trợ", "khiếu nại đơn hàng"]
    assert first.last_capability == "support.triage"
    assert second.history == ["báo cáo"]


# ---------------------------------------------------------------------------
# 3. Pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_result_list_sends_first_page_with_next_button(bot, ctx, monkeypatch):
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(8)))

    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội")
    await bot._message_handler(update, ctx)

    text = update.message.text
    assert "Quán ăn số 1" in text
    assert "Quán ăn số 5" in text
    assert "Quán ăn số 6" not in text, "page 1 must stop at PAGE_SIZE items"
    assert "Trang 1/2" in text

    buttons = _inline_buttons(update.message.markups[-1])
    labels = [label for label, _ in buttons]
    assert any("Tiếp" in label for label in labels), labels
    assert not any("Trước" in label for label in labels), "no prev button on page 1"
    assert buttons[-1][1].startswith("pg:1:")


@pytest.mark.asyncio
async def test_short_result_list_has_no_pagination_keyboard(bot, ctx, monkeypatch):
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(3)))
    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội")
    await bot._message_handler(update, ctx)
    assert "Trang 1/1" in update.message.text
    assert _inline_buttons(update.message.markups[-1]) == []


@pytest.mark.asyncio
async def test_pagination_callback_returns_next_page(bot, ctx, monkeypatch):
    """Bấm "Tiếp ▶️" -> trang 2 được lát từ session (không gọi lại web)."""
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(8)))
    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội", user_id=777)
    await bot._message_handler(update, ctx)
    next_data = [d for _, d in _inline_buttons(update.message.markups[-1])][-1]
    searches_before = [e for e in ctx.bot.events if e[0] == "web_search"]

    query = MockCallbackQuery(data=next_data, from_user=MockUser(id=777))
    await bot._pagination_callback(MockCallbackUpdate(callback_query=query), ctx)

    assert query.answered == 1, "callback must be acknowledged"
    page2 = query.edited[-1]
    assert "Quán ăn số 6" in page2
    assert "Quán ăn số 8" in page2
    assert "Quán ăn số 5" not in page2
    assert "Trang 2/2" in page2
    # Page 2 keyboard offers "< Trước" and no "Tiếp >" (last page).
    labels = [label for label, _ in _inline_buttons(query.markups[-1])]
    assert any("Trước" in label for label in labels), labels
    assert not any("Tiếp" in label for label in labels), labels
    # Session remembers the page and no extra web call was made.
    assert bot.sessions.get(777).page == 1
    assert [e for e in ctx.bot.events if e[0] == "web_search"] == searches_before


@pytest.mark.asyncio
async def test_pagination_callback_back_to_previous_page(bot, ctx, monkeypatch):
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(12)))
    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội", user_id=777)
    await bot._message_handler(update, ctx)

    token = bot._page_token("tìm quán ăn ngon ở Hà Nội")
    query = MockCallbackQuery(data=f"pg:1:{token}", from_user=MockUser(id=777))
    await bot._pagination_callback(MockCallbackUpdate(callback_query=query), ctx)
    assert "Trang 2/3" in query.edited[-1]

    back = MockCallbackQuery(data=f"pg:0:{token}", from_user=MockUser(id=777))
    await bot._pagination_callback(MockCallbackUpdate(callback_query=back), ctx)
    assert "Trang 1/3" in back.edited[-1]
    assert "Quán ăn số 1" in back.edited[-1]


@pytest.mark.asyncio
async def test_pagination_callback_clamps_out_of_range_page(bot, ctx, monkeypatch):
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, _results(8)))
    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội", user_id=777)
    await bot._message_handler(update, ctx)

    query = MockCallbackQuery(data="pg:99:deadbeef", from_user=MockUser(id=777))
    await bot._pagination_callback(MockCallbackUpdate(callback_query=query), ctx)
    assert "Trang 2/2" in query.edited[-1]


@pytest.mark.asyncio
async def test_pagination_callback_expired_session_is_friendly(bot, ctx):
    """No cached results (TTL hết hạn) -> nhắc user gửi lại, không crash."""
    query = MockCallbackQuery(data="pg:1:deadbeef", from_user=MockUser(id=4242))
    await bot._pagination_callback(MockCallbackUpdate(callback_query=query), ctx)
    assert "hết hạn" in query.edited[-1]


@pytest.mark.asyncio
async def test_button_callback_delegates_pagination(bot, ctx, monkeypatch):
    """The generic inline-menu router hands "pg:*" to the pagination handler."""
    bot.sessions.update(777, results=_results(8), last_query="tìm quán ăn", page=0)
    query = MockCallbackQuery(data="pg:1:abc12345", from_user=MockUser(id=777))
    await bot._button_callback(MockCallbackUpdate(callback_query=query), ctx)
    assert "Trang 2/2" in query.edited[-1]


def test_format_page_numbers_items_continuously(bot):
    items = [{"title": f"Mục {i}"} for i in range(1, 8)]
    page2 = bot._format_page(items, 1, header="🔎 Kết quả")
    assert "6. Mục 6" in page2
    assert "7. Mục 7" in page2
    assert "Trang 2/2" in page2
    assert page2.startswith("🔎 Kết quả")


def test_page_token_is_short_and_stable(bot):
    token = bot._page_token("Tìm  QUÁN ăn ngon")
    assert token == bot._page_token("tim quan an ngon")
    assert len(token) == 8
    # callback_data must stay well under Telegram's 64-byte limit.
    assert len(f"pg:12:{token}".encode()) < 64


# ---------------------------------------------------------------------------
# 4. Quick-reply keyboard
# ---------------------------------------------------------------------------

EXPECTED_LABELS = [
    tb.QUICK_REPLY_FOOD,
    tb.QUICK_REPLY_REPORT,
    tb.QUICK_REPLY_SUPPORT,
    tb.QUICK_REPLY_HEALTH,
]


def test_quick_reply_keyboard_has_the_four_vietnamese_actions(bot):
    labels = _keyboard_labels(bot._quick_reply_keyboard())
    assert labels == EXPECTED_LABELS
    assert labels == ["🔍 Tìm món ăn", "📊 Báo cáo", "❓ Hỗ trợ", "🩺 Sức khỏe hệ thống"]


@pytest.mark.asyncio
async def test_help_command_shows_quick_reply_keyboard(bot, ctx):
    update = MockUpdate.from_message("/help")
    await bot._help_command(update, ctx)
    # Existing contract (test_telegram_bot.py) still holds...
    assert "/health" in update.message.text
    assert "/report" in update.message.text
    assert "/research" in update.message.text
    # ...plus the new quick-reply keyboard.
    assert _keyboard_labels(update.message.markups[-1]) == EXPECTED_LABELS


@pytest.mark.asyncio
async def test_unknown_input_shows_quick_reply_keyboard(bot, ctx):
    update = MockUpdate.from_message("asdkjh qwe")
    await bot._friendly_unknown(update)
    assert "chưa rõ ý" in update.message.text
    assert _keyboard_labels(update.message.markups[-1]) == EXPECTED_LABELS


@pytest.mark.asyncio
async def test_unknown_intent_is_not_hijacked_to_a_wrong_handler(bot, ctx, monkeypatch):
    """Ý định không rõ -> giữ nguyên luồng chat cũ, không cướp sang report/health/kb.

    Fallback thân thiện (kèm bàn phím gợi ý) được kiểm ở
    test_unknown_input_shows_quick_reply_keyboard.
    """
    import packages.llm.factory as factory

    calls: list[str] = []

    def _recorder(name: str):
        async def _rec(self, update, context):
            calls.append(name)
            await update.message.reply_text(f"[{name}]")

        return _rec

    for name in ("_report_command", "_health_command", "_kb_command"):
        monkeypatch.setattr(MonitoringBot, name, _recorder(name))

    def _no_llm(settings):  # noqa: ANN001
        raise RuntimeError("no llm in tests")

    monkeypatch.setattr(factory, "get_llm_provider", _no_llm)

    update = MockUpdate.from_message("qwerty zxcvb khong ro y gi ca")
    await bot._message_handler(update, ctx)

    assert calls == [], calls
    text = update.message.text or ""
    # Ends in a friendly Vietnamese message, never a fabricated answer.
    assert "Xin lỗi" in text or "chưa rõ ý" in text


@pytest.mark.asyncio
async def test_quick_reply_report_button_routes_to_report(bot, ctx, monkeypatch):
    called: list[str] = []

    async def _fake_report(self, update, context):
        called.append("report")
        await update.message.reply_text("[report]")

    monkeypatch.setattr(MonitoringBot, "_report_command", _fake_report)
    update = MockUpdate.from_message(tb.QUICK_REPLY_REPORT)
    await bot._message_handler(update, ctx)
    assert called == ["report"]
    assert bot.sessions.get(777).last_capability == "reporting"


@pytest.mark.asyncio
async def test_quick_reply_health_button_routes_to_health(bot, ctx, monkeypatch):
    called: list[str] = []

    async def _fake_health(self, update, context):
        called.append("health")
        await update.message.reply_text("[health]")

    monkeypatch.setattr(MonitoringBot, "_health_command", _fake_health)
    update = MockUpdate.from_message(tb.QUICK_REPLY_HEALTH)
    await bot._message_handler(update, ctx)
    assert called == ["health"]
    assert bot.sessions.get(777).last_capability == "monitoring.health"


@pytest.mark.asyncio
async def test_quick_reply_support_button_gives_guidance(bot, ctx):
    update = MockUpdate.from_message(tb.QUICK_REPLY_SUPPORT)
    await bot._message_handler(update, ctx)
    assert "hỗ trợ" in update.message.text.lower()
    assert "/help" in update.message.text
    assert _keyboard_labels(update.message.markups[-1]) == EXPECTED_LABELS


# ---------------------------------------------------------------------------
# 5. Free-text intent routing (Vietnamese)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_text_report_intent_routes_to_report_command(bot, ctx, monkeypatch):
    called: list[str] = []

    async def _fake_report(self, update, context):
        called.append("report")
        await update.message.reply_text("[report]")

    monkeypatch.setattr(MonitoringBot, "_report_command", _fake_report)
    update = MockUpdate.from_message("cho tôi xem báo cáo tình hình hôm nay")
    await bot._message_handler(update, ctx)
    assert called == ["report"]


@pytest.mark.asyncio
async def test_free_text_health_intent_routes_to_health_command(bot, ctx, monkeypatch):
    called: list[str] = []

    async def _fake_health(self, update, context):
        called.append("health")
        await update.message.reply_text("[health]")

    monkeypatch.setattr(MonitoringBot, "_health_command", _fake_health)
    update = MockUpdate.from_message("kiểm tra hệ thống giúp mình với")
    await bot._message_handler(update, ctx)
    assert called == ["health"]


@pytest.mark.asyncio
async def test_free_text_knowledge_intent_routes_to_kb(bot, ctx, monkeypatch):
    called: list[str] = []

    async def _fake_kb(self, update, context):
        called.append(" ".join(context.args))
        await update.message.reply_text("[kb]")

    monkeypatch.setattr(MonitoringBot, "_kb_command", _fake_kb)
    update = MockUpdate.from_message("cho mình xem chính sách bảo hành")
    await bot._message_handler(update, ctx)
    assert called == ["cho mình xem chính sách bảo hành"]


@pytest.mark.asyncio
async def test_food_search_without_results_refuses_to_invent(bot, ctx, monkeypatch):
    """Không có nguồn thật -> nói rõ, KHÔNG bịa danh sách quán."""
    monkeypatch.setattr(tb, "_real_web_search", _fake_search(ctx.bot.events, []))
    update = MockUpdate.from_message("tìm quán ăn ngon ở Hà Nội")
    await bot._message_handler(update, ctx)
    assert "KHÔNG bịa" in update.message.text
    assert _keyboard_labels(update.message.markups[-1]) == EXPECTED_LABELS


@pytest.mark.asyncio
async def test_michelin_question_still_uses_strict_verify_path(bot, ctx, monkeypatch):
    """Câu có "món ăn/michelin" vẫn đi luồng verify cũ (không bị pagination cướp)."""
    seen: list[str] = []

    async def _search(query: str) -> list[dict]:
        seen.append(query)
        return []

    monkeypatch.setattr(tb, "_real_web_search", _search)
    # Ignore any answer persisted by earlier real runs so the web path is exercised.
    import packages.core.rag_cache as rag_cache

    monkeypatch.setattr(rag_cache, "rag_get", lambda *a, **k: None)
    monkeypatch.setattr(rag_cache, "rag_store", lambda *a, **k: None)
    update = MockUpdate.from_message("các món ăn Việt Nam lọt vào Michelin")
    await bot._message_handler(update, ctx)
    assert seen and "michelin guide vietnam" in seen[0]
    assert "Trang 1/" not in (update.message.text or "")