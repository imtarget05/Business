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
        self._pending_jobsearch: dict[int, dict] = {}  # chat_id -> {target_mail, text}
    
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
            elif d == "jobsearch_cancel":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                self._pending_jobsearch.pop(chat_id2, None)
                await q.edit_message_text("❌ Đã hủy JobSearch. Gửi lại brief khi bạn sẵn sàng.", reply_markup=self._main_menu_keyboard())
            elif d == "jobsearch_confirm":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                pending = self._pending_jobsearch.pop(chat_id2, None)
                if not pending:
                    await q.edit_message_text("⚠️ Không tìm thấy brief. Gửi lại brief AI Intern.", reply_markup=self._main_menu_keyboard())
                    return
                target_mail = pending.get("target_mail", "binhtan5734@gmail.com")
                await q.edit_message_text(f"🔍 Đang search 10 job VERIFIED cho *{target_mail}* (2-3 phút)...", parse_mode=ParseMode.MARKDOWN)
                import asyncio as _aio2, uuid as _uuid2, datetime as _dt2, json as _json2, pathlib as _pl2
                import httpx as _httpx2
                async def _do_jobsearch_confirm():
                    try:
                        from packages.core.bootstrap import get_container
                        from packages.contracts.models import TaskRequest, TaskContext
                        from packages.contracts.enums import Domain
                        ctn = get_container()
                        queries = ["AI Intern Ho Chi Minh Vietnam","Machine Learning Intern Hanoi ITviec","Generative AI Intern Vietnam TopCV","MLOps Intern Vietnam"]
                        all_items: list[dict] = []
                        for _q in queries:
                            try:
                                _req = TaskRequest(task_id=_uuid2.uuid4(), domain=Domain.RESEARCH, action="web_search", payload={"query": _q, "limit": 5}, context=TaskContext(organization_id=_uuid2.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                                _desc, _handler = ctn.registry.get_by_capability("research.web_search")
                                _resp = await _handler.handle(_req)
                                if _resp.result and _resp.result.get("results"):
                                    for it in _resp.result["results"]:
                                        it["_q"] = _q
                                        all_items.append(it)
                            except Exception:
                                continue
                        # inform searching
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=f"🔎 Đã search {len(all_items)} nguồn, đang verify link Apply + chấm 0-100...")
                        except Exception:
                            pass
                        seen_url: set[str] = set()
                        uniq: list[dict] = []
                        for it in all_items:
                            u = it.get("url","")
                            if u and u not in seen_url and "example.com" not in u:
                                seen_url.add(u)
                                uniq.append(it)
                        verified: list[dict] = []
                        audit: list[dict] = []
                        now = _dt2.datetime.now(_dt2.timezone.utc).isoformat()
                        bg_keywords = ["python","docker","kubernetes","pytorch","computer vision","machine learning","mlops","llm","generative ai","agent","cloud"]
                        # helper: chi verify trang chi tiet, bo listing
                        def _is_detail(u: str) -> bool:
                            low = u.lower()
                            if "topcv.vn/viec-lam/" in low and ".html" in low:
                                return True
                            if "itviec.com/it-jobs/" in low:
                                path = low.split("it-jobs/")[1].split("?")[0].split("#")[0].strip("/")
                                # detail co slug dai va nhieu dau -
                                if path in ["machine-learning","generative-ai","python","ai","jobs"]: return False
                                if path.count("-") >= 2 and len(path) > 12: return True
                                return False
                            if "linkedin.com/jobs/view/" in low: return True
                            if "vietnamworks.com" in low and "/job" in low: return True
                            # loai listing
                            if "tim-viec-lam" in low: return False
                            if "q=" in low and "indeed.com" in low: return False
                            if low.endswith("/it-jobs") or low.endswith("/it-jobs/"): return False
                            return False
                        import re as _re_title
                        for it in uniq[:25]:
                            url = it.get("url","")
                            # bo listing truoc khi verify
                            is_detail = _is_detail(url)
                            orig_title = it.get("title","") or it.get("snippet","") or url.split("/")[2]
                            title = orig_title
                            status = "UNCERTAIN"
                            evidence = f"search {it.get('_q','')} found {url}"
                            confidence = 0.6
                            html_title = ""
                            # fetch de lay title that + check Apply
                            try:
                                async with _httpx2.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as _cli:
                                    _r = await _cli.get(url)
                                    if _r.status_code == 200:
                                        _html = _r.text
                                        _html_low = _html.lower()
                                        # lay <title> thuc
                                        m_t = _re_title.search(r"<title[^>]*>(.*?)</title>", _html, flags=_re_title.IGNORECASE | _re_title.DOTALL)
                                        if m_t:
                                            html_title = _re_title.sub(r"<[^>]+>", "", m_t.group(1)).strip()[:120]
                                            if html_title and len(html_title) > 10:
                                                title = html_title
                                        # neu khong phai detail thi khong VERIFIED du co Apply
                                        has_apply = any(k in _html_low for k in ["apply","ứng tuyển","nộp đơn","apply now"])
                                        is_closed = any(k in _html_low for k in ["đã đóng","hết hạn","expired","closed","not found","404"])
                                        if not is_detail:
                                            status = "UNCERTAIN"
                                            confidence = 0.55
                                            evidence = f"listing page, not detail — checked {now} — title: {title[:60]}"
                                        elif has_apply and not is_closed:
                                            status = "VERIFIED"
                                            confidence = 0.92
                                            evidence = f"detail page 200 has Apply button, not closed — checked {now}"
                                        elif is_closed:
                                            status = "CLOSED"
                                            confidence = 0.85
                                            evidence = "page indicates closed/expired"
                                        else:
                                            status = "UNCERTAIN"
                                            confidence = 0.65
                                            evidence = "detail page 200 but no clear Apply button"
                                    else:
                                        status = "UNCERTAIN"
                                        evidence = f"http {_r.status_code}"
                            except Exception as _e:
                                evidence = f"fetch error: {_e}"
                            # chi dua VERIFIED detail vao list chinh
                            low_title = (title + " " + url).lower()
                            skill_match = sum(1 for k in bg_keywords if k in low_title) / max(1, len(bg_keywords)) * 40 + 50
                            if "intern" in low_title: skill_match += 10
                            if "ai" in low_title: skill_match += 10
                            match = int(min(95, max(55, skill_match)))
                            loc = "Ho Chi Minh" if "hcm" in low_title or "ho chi minh" in low_title else ("Ha Noi" if "hanoi" in low_title or "ha noi" in low_title else "Vietnam")
                            # tach company tu html title: thuong "Job - Company | Site"
                            company = "Unknown"
                            if " - " in title: company = title.split(" - ")[1].split("|")[0].split("-")[0].strip()[:40]
                            elif " tại " in title.lower(): company = title.lower().split(" tại ")[1].split("|")[0].strip()[:40].title()
                            else: company = orig_title.split("—")[0].strip()[:40] if "—" in orig_title else title.split("|")[0].strip()[:40]
                            if not company or len(company) < 3: company = url.split("/")[2]
                            job = {"company": company or "Unknown","job_title": title[:80],"location": loc,"work_type": "On-site","salary": "","deadline": "","required_skills": ", ".join([k for k in bg_keywords if k in low_title][:5]),"experience": "Intern","link": url,"checked_at": now,"evidence": evidence,"confidence": confidence,"status": status,"match": match,"source": it.get("_q","")}
                            if status == "VERIFIED":
                                verified.append(job)
                            audit.append({"url": url, "title": title, "search_timestamp": now, "verification_timestamp": now, "status": status, "evidence": evidence, "confidence": confidence})
                        verified = sorted(verified, key=lambda x: x["match"], reverse=True)[:10]
                        dedup: dict[str, dict] = {}
                        for j in verified:
                            key = f"{j['company'].lower()}|{j['job_title'].lower()}|{j['location'].lower()}"
                            if key not in dedup or j["match"] > dedup[key]["match"]:
                                dedup[key] = j
                        verified = list(dedup.values())[:10]
                        try:
                            _base = _pl2.Path("D:/Business Ops Agent Swarm") if _pl2.Path("D:/Business Ops Agent Swarm/job_search_results.json").parent.exists() else _pl2.Path(".")
                            (_base / "job_search_results.json").write_text(_json2.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "verified_jobs.json").write_text(_json2.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "job_audit_log.json").write_text(_json2.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                        if not verified:
                            try:
                                await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Không verify được job nào có nút Apply còn mở (đã check {len(uniq)} link). Mình không bịa — đây là danh sách nguồn để bạn tự check:\n" + "\n".join(f"• {u.get('url')}" for u in uniq[:5]))
                            except Exception:
                                pass
                            return
                        tbl = "| Rank | Company | Position | Location | Match | Status | Apply |\n|------|---------|----------|----------|-------|--------|-------|\n"
                        for idx, j in enumerate(verified[:5], 1):
                            tbl += f"| {idx} | {j['company']} | {j['job_title'][:30]} | {j['location']} | {j['match']} | {j['status']} | [Apply]({j['link']}) |\n"
                        top3_txt = ""
                        for j in verified[:3]:
                            top3_txt += f"\n**{j['company']} — {j['job_title']} ({j['match']}/100)**\n- Vì sao phù hợp: {j['required_skills'] or 'AI Intern, khớp background ML/Cloud'}\n- Skill thiếu: Kubernetes/MLOps chuyên sâu\n- CV hướng: nhấn Python + PyTorch + Docker + portfolio CV/LLM demo\n- Nên apply ngay: Có (VERIFIED còn mở)\n"
                        summary = f"**TOP AI INTERN JOBS (VERIFIED {len(verified)}/{len(uniq)})**\n\n{tbl}\n{top3_txt}\n\nTổng đã tìm: {len(uniq)} | VERIFIED: {len(verified)} | UNCERTAIN: {len([a for a in audit if a['status']=='UNCERTAIN'])} | CLOSED: {len([a for a in audit if a['status']=='CLOSED'])}\nTOP 3 nên apply: {', '.join(v['company'] for v in verified[:3])}\nSkill bổ sung: Kubernetes, MLOps (MLflow), LLM/RAG, Docker K8s"
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=summary[:4000], parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                        try:
                            from integrations.google_client import gmail_send
                            from packages.config.settings import get_settings
                            allowed = get_settings().gmail_allowed_recipients or []
                            email_body = summary + "\n\n" + "\n".join(f"{j['company']} | {j['job_title']} | {j['link']} | Match {j['match']} | {j['evidence']}" for j in verified[:5])
                            if target_mail.lower() not in [a.lower() for a in allowed]:
                                await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ {target_mail} chưa trong allowlist ({', '.join(allowed)}). Dùng /menu → ⚙️ Setup Mail → ➕ Thêm mail trước.")
                            else:
                                _res = gmail_send(to=target_mail, subject=f"[Business Ops] TOP {len(verified[:5])} AI Intern VERIFIED — {now[:10]}", body=email_body)
                                if _res.get("mode") == "DRY_RUN":
                                    await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gmail DRY_RUN, chưa gửi thật tới {target_mail}")
                                else:
                                    await context.bot.send_message(chat_id=chat_id2, text=f"✅ Đã gửi báo cáo TOP {len(verified[:5])} VERIFIED về {target_mail} (id {_res.get('id')})")
                        except Exception as _e:
                            try:
                                await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gửi mail lỗi: {_e}")
                            except Exception:
                                pass
                    except Exception as e2:
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=f"❌ JobSearch lỗi: {e2}")
                        except Exception:
                            pass
                _aio2.create_task(_do_jobsearch_confirm())
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
        # Job Search - hỏi trước khi làm (không tự chạy)
        if is_jobsearch:
            try:
                import re as _re_mail
                m_mail = _re_mail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                target_mail = m_mail.group(0) if m_mail else "tanmainguyenbinh@gmail.com"
                self._pending_jobsearch[chat_id] = {"target_mail": target_mail, "text": text}
                from telegram import InlineKeyboardButton as _B2, InlineKeyboardMarkup as _M2
                kb2 = _M2([
                    [_B2("✅ Bắt đầu search 10 job", callback_data="jobsearch_confirm"), _B2("❌ Hủy", callback_data="jobsearch_cancel")],
                ])
                await update.message.reply_text(
                    f"🔍 Đã nhận brief AI/ML Intern — sẽ search 10 job VERIFIED (ưu tiên TopCV/VietnamWorks/ITviec/LinkedIn), verify link Apply + chấm 0-100, rồi gửi báo cáo về *{target_mail}*.\n\nBạn có muốn bắt đầu ngay không?",
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb2,
                )
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
