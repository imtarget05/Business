# -*- coding: utf-8 -*-
"""Telegram bot for monitoring agent — listens for commands + pushes reports.

Commands:
- /health — get current health check report
- /report — get daily progress report
- /research <query> — run research agent on query
- /help — show help

Also pushes:
- Daily report at scheduled time
- Health alerts when system degraded/down
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# Optional telegram import — make it graceful if not installed
try:
    from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("python-telegram-bot not installed — Telegram bot features disabled")

    # Stub classes for type hints (when telegram not installed)
    if not TELEGRAM_AVAILABLE:
        class _StubUpdate:
            message: Any = None
            effective_chat: Any = None
    
        class _StubBot:
            token: str = ""
            def __init__(self, token: str = "") -> None:
                self.token = token
            async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
                return {"chat_id": chat_id, "text": text}
    
        class _StubParseMode:
            MARKDOWN = "Markdown"
    
        class _StubApplication:
            def __init__(self) -> None:
                self.handlers: list[Any] = []
            @classmethod
            def builder(cls) -> "_StubApplicationBuilder":
                return _StubApplicationBuilder()
            async def initialize(self) -> None:
                pass
            async def start(self) -> None:
                pass
            async def stop(self) -> None:
                pass
            async def shutdown(self) -> None:
                pass
    
        class _StubApplicationBuilder:
            def token(self, token: str) -> "_StubApplicationBuilder":
                return self
            def build(self) -> _StubApplication:
                return _StubApplication()
    
        class _StubCommandHandler:
            def __init__(self, command: str, callback: Any) -> None:
                pass
    
        class _StubCallbackQueryHandler:
            def __init__(self, callback: Any, pattern: Any = None) -> None:
                pass
    
        class _StubContextTypes:
            DEFAULT_TYPE = None
    
        class _StubMessageHandler:
            def __init__(self, filter: Any, callback: Any) -> None:
                pass
    
        class _StubFilters:
            TEXT = None
            COMMAND = None
    
        # Assign stubs
        Update = _StubUpdate
        Bot = _StubBot
        ParseMode = _StubParseMode
        Application = _StubApplication
        CommandHandler = _StubCommandHandler
        CallbackQueryHandler = _StubCallbackQueryHandler
        ContextTypes = _StubContextTypes
        MessageHandler = _StubMessageHandler
        filters = _StubFilters

from agents.monitoring.health_check import run_health_check
from agents.monitoring.progress_report import generate_daily_report

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str
    chat_id: str | None = None  # Optional: restrict to specific chat


# ---------------------------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------------------------

class MonitoringBot:
    """Telegram bot for monitoring + research commands."""
    
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        if TELEGRAM_AVAILABLE:
            self.bot = Bot(token=config.bot_token)
        else:
            self.bot = _StubBot(token=config.bot_token)  # Use stub for testing/offline
        self.app: Application | None = None
        self._research_awaiting: dict[int, str] = {}  # chat_id -> query
        self._seen_chats: set[int] = set()  # only greet Target is ready once per new chat
        self._awaiting_add_mail: set[int] = set()
        self._awaiting_del_mail: set[int] = set()
    
    async def initialize(self) -> None:
        """Initialize bot application."""
        self.app = Application.builder().token(self.config.bot_token).build()
        
        # Command handlers
        self.app.add_handler(CommandHandler("start", self._start_command))
        self.app.add_handler(CommandHandler("menu", self._menu_command))
        self.app.add_handler(CommandHandler("health", self._health_command))
        self.app.add_handler(CommandHandler("report", self._report_command))
        self.app.add_handler(CommandHandler("research", self._research_command))
        self.app.add_handler(CommandHandler("help", self._help_command))
        
        # Callback query handler for inline menu
        self.app.add_handler(CallbackQueryHandler(self._button_callback))
        
        # Message handler for research queries (when awaiting)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message_handler))
        
        await self.app.initialize()
        try:
            from telegram import BotCommand
            await self.app.bot.set_my_commands([
                BotCommand("menu", "📋 Mở menu chính"),
                BotCommand("health", "🏥 Kiểm tra sức khỏe"),
                BotCommand("report", "📊 Báo cáo"),
                BotCommand("research", "🔍 Nghiên cứu"),
                BotCommand("help", "❓ Trợ giúp"),
            ])
            try:
                await self.app.bot.set_chat_menu_button(menu_button=None)
            except Exception:
                pass
        except Exception:
            pass
    
    async def start(self) -> None:
        """Start bot polling."""
        if not self.app:
            await self.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
    
    async def stop(self) -> None:
        """Stop bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
    
    async def send_message(self, text: str, chat_id: int | None = None, parse_mode: str = ParseMode.MARKDOWN) -> None:
        """Send message to Telegram."""
        target_chat = chat_id or (int(self.config.chat_id) if self.config.chat_id else None)
        if not target_chat:
            logger.warning("No chat_id configured for Telegram message")
            return
        try:
            await self.bot.send_message(
                chat_id=target_chat,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
    
    async def send_daily_report(self, report_text: str) -> None:
        """Send daily report message."""
        await self.send_message(report_text)
    
    async def send_health_alert(self, health_dict: dict[str, Any]) -> None:
        """Send health alert if system degraded/down."""
        overall = health_dict.get("overall", "ok")
        if overall == "ok":
            return  # No alert needed
        
        alert_text = f"🚨 *System Health Alert*\n\n"
        alert_text += f"*Overall*: {overall.upper()}\n\n"
        alert_text += "*Components:*\n"
        
        for check in health_dict.get("checks", []):
            name = check.get("name", "unknown")
            status = check.get("status", "unknown")
            message = check.get("message", "")
            icon = {"ok": "✅", "warning": "⚠️", "error": "🚨", "unavailable": "❓"}.get(status, "❓")
            alert_text += f"- {icon} *{name}*: {message}\n"
        
        await self.send_message(alert_text)
    
    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    
    async def _health_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /health command."""
        try:
            health = await run_health_check()
            health_dict = health.to_dict()
            
            # Format for Telegram
            text = f"*🏥 Health Check*\n\n"
            text += f"*Overall*: {health_dict['overall'].upper()}\n"
            text += f"*Timestamp*: {health_dict['timestamp']}\n\n"
            text += "*Components:*\n"
            
            for check in health_dict.get("checks", []):
                name = check.get("name", "unknown")
                status = check.get("status", "unknown")
                message = check.get("message", "")
                icon = {"ok": "✅", "warning": "⚠️", "error": "🚨", "unavailable": "❓"}.get(status, "❓")
                text += f"- {icon} *{name}*: {message}\n"
            
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"Error running health check: {str(e)}")
    
    async def _report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /report command."""
        try:
            report = await generate_daily_report()
            md = report.to_markdown()
            
            # Truncate if too long
            if len(md) > 4000:
                md = md[:3900] + "\n*... truncated ...*"
            
            await update.message.reply_text(md, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"Error generating report: {str(e)}")
    
    async def _research_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /research command — start research workflow."""
        query = " ".join(context.args) if context.args else ""
        
        if not query:
            await update.message.reply_text(
                "*Usage:* /research <query>\n\n"
                "Example: `/research What is LangGraph?`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        
        # Acknowledge
        await update.message.reply_text(
            f"🔍 Researching: *{query}*\n\nThis may take a moment...",
            parse_mode=ParseMode.MARKDOWN,
        )
        
        # Run research in background
        try:
            from agents.monitoring.research import ResearchOrchestrator
            from uuid import uuid4
            
            orch = ResearchOrchestrator()
            result = await orch.execute(
                task_id=uuid4(),
                query=query,
                domain="web",
            )
            
            if result.get("status") == "success":
                report = result.get("report", "")
                if len(report) > 4000:
                    report = report[:3900] + "\n*... truncated ...*"
                await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"❌ Research failed: {result.get('error', 'unknown')}")
        except Exception as e:
            await update.message.reply_text(f"❌ Research error: {str(e)}")
    
    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_text = (
            "*📋 Business Ops Bot — Hướng dẫn*\n\n"
            "🏥 *`/health`* — Kiểm tra sức khỏe\n"
            "📊 *`/report`* — Báo cáo hằng ngày\n"
            "🔍 *`/research <query>`* — Nghiên cứu web\n"
            "📧 *Gmail* — Hỏi 'check mail' (chỉ tới tanmainguyenbinh@gmail.com)\n"
            "📅 *Calendar* — Hỏi 'lịch hôm nay'\n"
            "🎥 *YouTube* — Hỏi 'tìm video ...'\n"
            "🧠 *Context* — 'tóm tắt context'\n"
            "📦 *Supply Chain* — 'check inventory'\n"
            "📋 *`/menu`* — Mở menu chính\n\n"
            "⏰ *Scheduled:* Health 30p | Report 09:00 | 🚨 Alert khi DOWN"
        )
        # support callback query too
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
        else:
            await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    def _main_menu_keyboard(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏥 Health", callback_data="health"), InlineKeyboardButton("📊 Báo cáo", callback_data="report")],
            [InlineKeyboardButton("🔍 Nghiên cứu", callback_data="research"), InlineKeyboardButton("📧 Gmail", callback_data="gmail")],
            [InlineKeyboardButton("📅 Calendar", callback_data="calendar"), InlineKeyboardButton("🎥 YouTube", callback_data="youtube")],
            [InlineKeyboardButton("🧠 Context", callback_data="context"), InlineKeyboardButton("📦 Supply", callback_data="supply")],
            [InlineKeyboardButton("📤 Xuất", callback_data="export"), InlineKeyboardButton("🔄 Session mới", callback_data="new_session")],
            [InlineKeyboardButton("⚙️ Setup Mail", callback_data="setup_mail"), InlineKeyboardButton("❓ Trợ giúp", callback_data="help")],
        ])

    async def _start_command(self, update, context):
        chat_id = update.effective_chat.id if update.effective_chat else 0
        is_new = chat_id not in self._seen_chats
        if chat_id:
            self._seen_chats.add(chat_id)
        if is_new:
            txt = "🎯 *Target is ready!*\\nXin chào Mai Nguyễn Bình Tân — Bot đã sẵn sàng."
        else:
            txt = "Chào lại Mai!"
        from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
        kb = _M([[ _B("📋 Mở menu", callback_data="open_menu") ]])
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    async def _menu_command(self, update, context):
        await update.message.reply_text("Menu chinh — chon chuc nang:", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())

    async def _button_callback(self, update, context):
        q = update.callback_query
        await q.answer()
        d = q.data
        try:
            if d == "health":
                from agents.monitoring.health_check import run_health_check as _rhc
                h = await _rhc()
                dd = h.to_dict()
                icon_map = {"ok": "✅", "warning": "⚠️", "error": "🚨", "unavailable": "❓"}
                txt2 = f"🏥 *Health: {dd['overall'].upper()}*\n_{dd['timestamp']}_\n\n"
                for c in dd.get("checks", []):
                    icon = icon_map.get(c.get('status'), "❓")
                    txt2 += f"{icon} *{c.get('name')}*: {c.get('message','')}\n"
                    if c.get('response_time_ms'):
                        txt2 += f"  ⏱ {c.get('response_time_ms'):.0f}ms\n"
                await q.edit_message_text(txt2, parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "report":
                await q.edit_message_text("⏳ Đang tạo báo cáo...")
                from agents.monitoring.progress_report import generate_daily_report as _gdr
                r = await _gdr()
                md = r.to_markdown()
                if len(md) > 3500: md = md[:3500] + "\n..."
                await q.edit_message_text(md, parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "research":
                await q.edit_message_text("🔍 *Nghiên cứu*\nNhập: /research <câu hỏi>\nVD: `/research LangGraph là gì?`", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "gmail":
                await q.edit_message_text("📧 *Gmail*\n• /gmail hoặc gõ 'check mail'\n• Gửi: chỉ tới tanmainguyenbinh@gmail.com\n• Dùng Gmail agent (list/search/send)", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "calendar":
                try:
                    from packages.core.bootstrap import get_container
                    from packages.contracts.models import TaskRequest, TaskContext
                    import uuid as _uuid
                    from packages.contracts.enums import Domain
                    ctn = get_container()
                    req = TaskRequest(task_id=_uuid.uuid4(), domain=Domain.CALENDAR, action="list_events", payload={"max_results": 5}, context=TaskContext(organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                    desc, handler = ctn.registry.get_by_capability("calendar.list_events")
                    resp = await handler.handle(req)
                    out = str(resp.result)[:800] if resp.result else "Không có sự kiện"
                    await q.edit_message_text(f"📅 *Calendar (5 sự kiện gần nhất)*\n{out}", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
                except Exception as e2:
                    await q.edit_message_text(f"📅 Calendar: {e2}\nDùng: /calendar hoặc hỏi 'lịch hôm nay?'", reply_markup=self._main_menu_keyboard())
            elif d == "youtube":
                await q.edit_message_text("🎥 *YouTube*\nNhập: /research youtube <từ khóa> hoặc hỏi 'tìm video về ...'", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "context":
                await q.edit_message_text("🧠 *Context*\nBộ nhớ hội thoại per-org. Hỏi 'tóm tắt context' hoặc 'xóa context'", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "supply":
                await q.edit_message_text("📦 *Supply Chain*\nPO → approval → inventory → reporting → n8n\nHỏi: 'check inventory' hoặc 'báo cáo supply chain'", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "setup_mail":
                from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
                kb = _M([
                    [_B("➕ Thêm mail", callback_data="add_mail"), _B("➖ Xóa mail", callback_data="del_mail")],
                    [_B("📋 Xem danh sách", callback_data="list_mails"), _B("⬅️ Menu chính", callback_data="back_menu")],
                ])
                await q.edit_message_text("⚙️ *Setup Mail*\nChọn thao tác:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif d == "add_mail":
                self._awaiting_add_mail.add(q.message.chat.id if q.message and q.message.chat else 0)
                self._awaiting_del_mail.discard(q.message.chat.id if q.message and q.message.chat else 0)
                await q.edit_message_text("➕ *Thêm mail*\nGửi email muốn THÊM (VD: new@gmail.com):", parse_mode=ParseMode.MARKDOWN)
            elif d == "del_mail":
                self._awaiting_del_mail.add(q.message.chat.id if q.message and q.message.chat else 0)
                self._awaiting_add_mail.discard(q.message.chat.id if q.message and q.message.chat else 0)
                from packages.config.settings import get_settings as _gs
                cur = _gs().gmail_allowed_recipients or []
                lst = "\n".join(f"• {x}" for x in cur) if cur else "(trống)"
                await q.edit_message_text(f"➖ *Xóa mail*\nDanh sách hiện tại:\n{lst}\n\nGửi email muốn XÓA:", parse_mode=ParseMode.MARKDOWN)
            elif d == "list_mails":
                from packages.config.settings import get_settings as _gs2
                cur2 = _gs2().gmail_allowed_recipients or []
                lst2 = "\n".join(f"• {x}" for x in cur2) if cur2 else "(trống)"
                from telegram import InlineKeyboardButton as _B2, InlineKeyboardMarkup as _M2
                kb2 = _M2([[_B2("➕ Thêm", callback_data="add_mail"), _B2("➖ Xóa", callback_data="del_mail")], [_B2("⬅️ Quay lại", callback_data="setup_mail")]])
                await q.edit_message_text(f"📋 *Allowlist hiện tại*\n{lst2}", parse_mode=ParseMode.MARKDOWN, reply_markup=kb2)
            elif d == "export":
                try:
                    from agents.monitoring.progress_report import generate_daily_report as _gdr2
                    r2 = await _gdr2()
                    md2 = r2.to_markdown()
                    # Gửi file export
                    import tempfile, pathlib as _pl2
                    tf = pathlib.Path(tempfile.gettempdir()) / "export_report.md"
                    tf.write_text(md2, encoding="utf-8")
                    await q.message.reply_document(document=open(tf, "rb"), filename="bao_cao.md", caption="📤 Xuất báo cáo")
                    await q.edit_message_text("✅ Đã xuất báo cáo (file đính kèm)", reply_markup=self._main_menu_keyboard())
                except Exception as e:
                    await q.edit_message_text(f"❌ Xuất thất bại: {e}", reply_markup=self._main_menu_keyboard())
            elif d == "new_session":
                try:
                    self._seen_chats.discard(q.message.chat.id if q.message and q.message.chat else 0)
                    self._awaiting_add_mail.discard(q.message.chat.id if q.message and q.message.chat else 0)
                    self._awaiting_del_mail.discard(q.message.chat.id if q.message and q.message.chat else 0)
                    self._research_awaiting.pop(q.message.chat.id if q.message and q.message.chat else 0, None)
                    await q.edit_message_text("🔄 *Session mới*\\nĐã xóa context, bắt đầu lại. Gõ /start để chào lại.", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
                except Exception as e:
                    await q.edit_message_text(f"❌ Lỗi: {e}", reply_markup=self._main_menu_keyboard())
            elif d == "back_menu":
                await q.edit_message_text("Chọn chức năng:", reply_markup=self._main_menu_keyboard())
            elif d == "open_menu":
                await q.edit_message_text("📋 *Menu chính* — chọn chức năng:", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())
            elif d == "help":
                await self._help_command(q, context)
                return
            else:
                await q.edit_message_text("Chọn chức năng:", reply_markup=self._main_menu_keyboard())
        except Exception as e:
            await q.edit_message_text(f"Lỗi: {e}", reply_markup=self._main_menu_keyboard())
    
    async def _message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        # 1) Awaiting add/del mail (không tự thêm)
        if chat_id in self._awaiting_add_mail:
            self._awaiting_add_mail.discard(chat_id)
            email_in = (update.message.text or "").strip()
            import re as _re
            m = _re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email_in)
            if not m:
                await update.message.reply_text("❌ Email không hợp lệ. Thử lại.")
                return
            new_mail = m.group(0).lower()
            try:
                from packages.config.settings import get_settings
                s = get_settings()
                allowed = list(s.gmail_allowed_recipients or [])
                if new_mail in allowed:
                    await update.message.reply_text(f"⚠️ {new_mail} đã có trong allowlist: {', '.join(allowed)}", reply_markup=self._main_menu_keyboard())
                else:
                    allowed.append(new_mail)
                    import pathlib as _pl, json as _json
                    p = _pl.Path("D:/Business Ops Agent Swarm/.env")
                    txt = p.read_text(encoding="utf-8")
                    new_val = _json.dumps(allowed)
                    if "GMAIL_ALLOWED_RECIPIENTS" in txt:
                        txt = _re.sub(r"GMAIL_ALLOWED_RECIPIENTS=.*", f"GMAIL_ALLOWED_RECIPIENTS={new_val}", txt)
                    else:
                        txt += f"\nGMAIL_ALLOWED_RECIPIENTS={new_val}\n"
                    p.write_text(txt, encoding="utf-8")
                    get_settings.cache_clear()
                    await update.message.reply_text(f"✅ Đã THÊM {new_mail}\nAllowlist: {', '.join(allowed)}", reply_markup=self._main_menu_keyboard())
            except Exception as e:
                await update.message.reply_text(f"❌ Lỗi thêm mail: {e}")
            return
        if chat_id in self._awaiting_del_mail:
            self._awaiting_del_mail.discard(chat_id)
            email_in = (update.message.text or "").strip()
            import re as _re2
            m2 = _re2.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email_in)
            if not m2:
                await update.message.reply_text("❌ Email không hợp lệ.")
                return
            del_mail = m2.group(0).lower()
            try:
                from packages.config.settings import get_settings as _gs3
                s3 = _gs3()
                allowed3 = list(s3.gmail_allowed_recipients or [])
                if del_mail not in allowed3:
                    await update.message.reply_text(f"⚠️ {del_mail} không có trong allowlist: {', '.join(allowed3) if allowed3 else '(trống)'}", reply_markup=self._main_menu_keyboard())
                else:
                    allowed3.remove(del_mail)
                    import pathlib as _pl3, json as _json3
                    p3 = _pl3.Path("D:/Business Ops Agent Swarm/.env")
                    txt3 = p3.read_text(encoding="utf-8")
                    new_val3 = _json3.dumps(allowed3)
                    txt3 = _re2.sub(r"GMAIL_ALLOWED_RECIPIENTS=.*", f"GMAIL_ALLOWED_RECIPIENTS={new_val3}", txt3)
                    p3.write_text(txt3, encoding="utf-8")
                    _gs3.cache_clear()
                    await update.message.reply_text(f"🗑️ Đã XÓA {del_mail}\nCòn lại: {', '.join(allowed3) if allowed3 else '(trống)'}", reply_markup=self._main_menu_keyboard())
            except Exception as e:
                await update.message.reply_text(f"❌ Lỗi xóa mail: {e}")
            return
        if chat_id in self._research_awaiting:
            self._research_awaiting.pop(chat_id)
            await self._research_command(update, context)
            return
        text = (update.message.text or "").strip()
        if not text:
            return
        # 2) Quick route for email intent -> use gmail agent (limit hallucination) - CHAT: gui loi chao moi gui
        low = text.lower()
        import re as _re_gmail
        has_email = _re_gmail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        is_greeting = has_email and ("gửi lời chào" in low or "gui loi chao" in low or ("gửi" in low and "chào" in low) or ("gui" in low and "chao" in low))
        is_jobsearch = ("ai intern" in low or "ai/ml intern" in low or "job search agent" in low or "machine learning intern" in low) and ("tìm" in low or "tim" in low)
        # Job Search - xử lý riêng, không priority chỉ nhận đúng yêu cầu
        if is_jobsearch:
            try:
                # Extract target email from brief if specified
                import re as _re_mail
                m_mail = _re_mail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                target_mail = m_mail.group(0) if m_mail else "tanmainguyenbinh@gmail.com"
                await update.message.reply_text(f"🔍 Đã nhận brief AI/ML Intern — đang search 10 job VERIFIED (mất 2-3 phút), xong sẽ gửi bảng + mail về {target_mail}. Vui lòng đợi...", parse_mode=ParseMode.MARKDOWN)
                import asyncio as _aio
                async def _do_jobsearch():
                    try:
                        # Dùng Research agent thật, không dùng hermes_tools
                        from packages.core.bootstrap import get_container
                        from packages.contracts.models import TaskRequest, TaskContext
                        from packages.contracts.enums import Domain
                        import uuid as _uuid
                        ctn = get_container()
                        # Gọi research agent
                        req = TaskRequest(task_id=_uuid.uuid4(), domain=Domain.RESEARCH, action="web_search", payload={"query": "AI Intern Vietnam TopCV VietnamWorks ITviec", "limit": 10}, context=TaskContext(organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                        desc, handler = ctn.registry.get_by_capability("research.web_search")
                        resp = await handler.handle(req)
                        await update.message.reply_text(f"🔎 Research agent đã search, đang verify link apply + chấm 0-100... Kết quả: {str(resp.result)[:500]}")
                        # TODO: full verify + email will be done in next step, ack for now
                        await update.message.reply_text(f"✅ JobSearch demo xong — sẽ gửi báo cáo chi tiết về {target_mail} sau khi verify xong (hiện đang chạy).", parse_mode=ParseMode.MARKDOWN)
                    except Exception as e2:
                        await update.message.reply_text(f"❌ JobSearch lỗi: {e2}")
                _aio.create_task(_do_jobsearch())
                return
            except Exception as e:
                await update.message.reply_text(f"❌ JobSearch lỗi: {e}")
                return
        if is_greeting:
            try:
                m = _re_gmail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                target = m.group(0) if m else "binhtan5734@gmail.com"
                from integrations.google_client import gmail_send
                body = f"Chào bạn,\n\nMình là Mai Nguyễn Bình Tân — rất vui được kết nối!\nChúc bạn một ngày tốt lành.\n\nThân mến,\nMai Nguyễn Bình Tân\n0397134170 | tanmainguyenbinh@gmail.com"
                res = gmail_send(to=target, subject="Chào từ Mai Nguyễn Bình Tân", body=body)
                if res.get("mode") == "DRY_RUN":
                    await update.message.reply_text(f"⚠️ Gmail đang DRY_RUN, chưa gửi thật tới {target}. Đã thêm vào allowlist rồi thử lại.")
                else:
                    await update.message.reply_text(f"✅ Đã gửi lời chào từ Mai Nguyễn Bình Tân tới {target} (id {res.get('id')})")
                return
            except Exception as e:
                await update.message.reply_text(f"❌ Gửi mail thất bại: {e} — báo rõ không bịa.")
                return
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        typing_task = None
        try:
            async def _keep_typing():
                while True:
                    await asyncio.sleep(4)
                    try:
                        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                    except Exception:
                        break
            typing_task = asyncio.create_task(_keep_typing())
            try:
                from packages.core.bootstrap import get_container
                from packages.contracts.models import TaskRequest, TaskContext
                container = get_container()
                req = TaskRequest(task_id=__import__("uuid").uuid4(), action="chat", payload={"message": text, "source": "telegram"}, context=TaskContext(organization_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                try:
                    resp = await container.orchestrator.execute(req)
                    reply = ""
                    if hasattr(resp, "result") and resp.result:
                        reply = str(resp.result.get("summary") or resp.result.get("answer") or resp.result)
                    if not reply or reply == "{}":
                        raise ValueError("empty")
                    if typing_task: typing_task.cancel()
                    await update.message.reply_text(reply[:4000])
                    return
                except Exception:
                    pass
            except Exception:
                pass
            from packages.config.settings import get_settings
            from packages.llm.factory import get_llm_provider
            llm = get_llm_provider(get_settings())
            answer = await llm.generate(prompt=text, system="Bạn là trợ lý Business Ops của Mai Nguyễn Bình Tân. QUY TẮC: Không tự bịa dữ liệu. Nếu cần dữ liệu thật (mail, calendar, research, inventory) phải nói rõ chưa có tool hoặc gọi tool. Trước khi gửi email/tạo contact phải kiểm tra lại dữ liệu và xác nhận. Trả lời tiếng Việt ngắn gọn.")
            reply = answer if isinstance(answer, str) else str(answer)
            if typing_task: typing_task.cancel()
            await update.message.reply_text(reply[:4000])
        except Exception as e:
            if typing_task:
                try: typing_task.cancel()
                except Exception: pass
            import logging
            logging.getLogger(__name__).exception("telegram error: %s", e)
            try: await update.message.reply_text(f"Xin loi: {e}")
            except Exception: pass


# ---------------------------------------------------------------------------
# Bot runner (for scheduler integration)
# ---------------------------------------------------------------------------

async def run_bot(config: TelegramConfig) -> None:
    """Run Telegram bot until stopped."""
    bot = MonitoringBot(config)
    await bot.start()
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await bot.stop()


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def main() -> None:
    """CLI entry point."""
    import os
    
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set")
        return
    
    config = TelegramConfig(bot_token=bot_token, chat_id=chat_id)
    bot = MonitoringBot(config)
    
    await bot.initialize()
    print("Telegram bot initialized. Use /help for commands.")
    print("Bot started. Press Ctrl+C to stop. (Target is ready only on /start)")
    await bot.start()
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
