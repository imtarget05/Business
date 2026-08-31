"""Integration test: V2 verify-gate must NOT auto-send email when 0 VERIFIED.

This is the silent-contract-violation guard. The confirm screen promises
"sẽ xác minh trước khi gửi"; if verification yields 0 VERIFIED listings, the
MUST stop and ask the user — it must NOT call gmail_send on its own.

Runs the real pipeline (real web_search + web_extract via HttpxWebTools) but
patches gmail_send with unittest.mock so we can assert it was never called
without explicit user consent (the jobsearch_send_unconfirmed button).
"""

from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, ".")

SENT: list[str] = []
GMAIL_CALLS: list[str] = []


def _make_bot():
    from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig

    bot = MonitoringBot(TelegramConfig(bot_token="STUB"))

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            SENT.append(text)
            return {"text": text}

        async def send_chat_action(self, chat_id, action):
            return None

    bot.bot = _Bot()
    return bot


def _cb(data):
    class M:
        chat_id = 1
        message_id = 1

        async def reply_text(self, t, **kw):
            SENT.append(t)
            return {"text": t}

        chat = type("C", (), {"id": 1})()

    class CB:
        async def answer(self):
            pass

        async def edit_message_text(self, t, **kw):
            SENT.append(t)
            return {"text": t}

    cb = CB()
    cb.data = data
    m = M()
    cb.message = m
    cb.effective_chat = type("C", (), {"id": 1})()

    class U:
        callback_query = cb
        message = m
        effective_chat = type("C", (), {"id": 1})()

    return U()


async def _run_confirm(bot, chat):
    SENT.clear()
    GMAIL_CALLS.clear()
    import asyncio as _a

    tasks = []
    _o = _a.create_task

    def _c(coro, *a, **k):
        t = _o(coro, *a, **k)
        tasks.append(t)
        return t

    _a.create_task = _c
    await bot._button_callback(_cb("jobsearch_confirm"), type("C", (), {"bot": bot.bot})())
    for t in tasks:
        try:
            await t
        except Exception as e:
            print("TASK ERR:", repr(e)[:200])
    _a.create_task = _o


def test_v2_gate_blocks_auto_send_when_zero_verified():
    """Real pipeline, 0 VERIFIED -> no gmail_send, user gets an ASK prompt."""
    pytest.importorskip("telegram")
    spy = mock.MagicMock(return_value={"mode": "DRY_RUN", "id": "spy-000", "to": None})
    with mock.patch("integrations.google_client.gmail_send", spy):
        bot = _make_bot()
        chat = 1
        bot._pending_jobsearch[chat] = {
            "target_mail": "tanmainguyenbinh@gmail.com",
            "text": "tìm marketing hà nội còn apply được",
        }
        asyncio.run(_run_confirm(bot, chat))

    joined = "\n".join(SENT)
    # 1) Must NOT have silently sent email
    assert spy.call_count == 0, f"gmail_send called WITHOUT user consent: {spy.call_args_list}"
    # 2) Must show the honest stats line (V3)
    assert "Thống kê" in joined, "missing drop-reason stats"
    # 3) Must stop and ask the user (V2 gate)
    assert "CHƯA gửi email" in joined or "Bạn muốn" in joined, "gate did not ask user"
    # 4) Must admit core question unanswered (V5)
    assert "chưa trả lời được" in joined.lower() or "CHƯA XÁC NHẬN" in joined


def test_v2_user_can_still_send_unconfirmed_explicitly():
    """Explicit consent (jobsearch_send_unconfirmed) DOES call gmail_send.

    Isolated: we pre-seed _last_jobsearch with a fake candidate and patch the
    allowlist, so we test ONLY the consent->send wiring without the network.
    """
    pytest.importorskip("telegram")
    spy = mock.MagicMock(return_value={"mode": "DRY_RUN", "id": "spy-001", "to": None})
    _real = None
    try:
        import packages.config.settings as _st

        _real = _st.get_settings()
        _restore = list(getattr(_real, "gmail_allowed_recipients", []) or [])
        _real.gmail_allowed_recipients = ["tanmainguyenbinh@gmail.com"]

        bot = _make_bot()
        chat = 1
        bot._pending_jobsearch[chat] = {
            "target_mail": "tanmainguyenbinh@gmail.com",
            "text": "tìm marketing hà nội còn apply được",
        }
        # Seed the gate output as if the pipeline had stopped & asked.
        bot._last_jobsearch[chat] = [
            {
                "job_title": "Marketing Hà Nội",
                "company": "ACME",
                "link": "https://topcv.vn/viec-lam/mkt.html",
                "status": "UNCERTAIN",
                "match": 0.7,
            }
        ]
        with mock.patch("integrations.google_client.gmail_send", spy):
            import asyncio as _a

            tasks = []
            _o = _a.create_task

            def _c(coro, *a2, **k):
                t = _o(coro, *a2, **k)
                tasks.append(t)
                return t

            _a.create_task = _c

            async def _fire():
                await bot._button_callback(
                    _cb("jobsearch_send_unconfirmed"), type("C", (), {"bot": bot.bot})()
                )
                for t in tasks:
                    try:
                        await t
                    except Exception:
                        pass

            asyncio.run(_fire())
            _a.create_task = _o
    finally:
        if _real is not None:
            import packages.config.settings as _st

            try:
                _st.get_settings().gmail_allowed_recipients = _restore
            except Exception:
                pass

    assert spy.call_count >= 1, "explicit send-unconfirmed should call gmail_send"
    assert spy.call_args_list[0].kwargs.get("to") == "tanmainguyenbinh@gmail.com"
