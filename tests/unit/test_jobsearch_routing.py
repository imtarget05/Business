"""Routing priority: JobSearch intent must win over Advisory for hiring phrases.

Bug history: "tìm marketing hà nội còn apply được" was hijacked by the Advisory
persona router (marketing = a persona keyword) instead of JobSearch. JobSearch
intent (hiring phrase) must take priority.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig

SENT: list[str] = []


class _Bot:
    async def send_message(self, chat_id, text, **kw):
        SENT.append(text)
        return {"text": text}

    async def send_chat_action(self, chat_id, action):
        return None


def _make_bot():
    bot = MonitoringBot(TelegramConfig(bot_token="STUB"))
    bot.bot = _Bot()
    return bot


def _fake_update(text):
    class M:
        chat_id = 1
        message_id = 1

        async def reply_text(self, t, **kw):
            SENT.append(t)
            return {"text": t}

    m = M()
    m.text = text

    class C:
        id = 1

    class U:
        message = m
        effective_chat = C()
        effective_user = C()

    return U()


async def _run(text):
    SENT.clear()
    bot = _make_bot()
    upd = _fake_update(text)
    await bot._message_handler(upd, type("Ctx", (), {"bot": bot.bot})())
    return list(SENT)


def test_jobsearch_priority_over_advisory():
    out = asyncio.run(_run("tìm marketing hà nội còn apply được"))
    joined = "\n".join(out)
    assert "Xác nhận tìm kiếm việc làm" in joined, f"expected JobSearch screen, got: {out}"
    assert "Gary Vee" not in joined and "Advisory Council" not in joined, (
        f"advisory hijacked: {out}"
    )


def test_jobsearch_catch_apply_phrase():
    out = asyncio.run(_run("tìm 5 job ai intern còn apply được tại Hà Nội"))
    joined = "\n".join(out)
    assert "Xác nhận tìm kiếm việc làm" in joined, f"got: {out}"


def test_advisory_still_works_for_pure_persona():
    out = asyncio.run(_run("tư vấn chiến lược marketing theo Buffett"))
    joined = "\n".join(out)
    assert "Advisory" in joined or "Gary" in joined or "persona" in joined.lower(), f"got: {out}"
