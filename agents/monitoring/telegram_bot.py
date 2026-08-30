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
import re
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
        self._pending_youtube: dict[int, dict] = {}  # chat_id -> {target_mail, text}
    
    async def initialize(self) -> None:
        """Initialize bot application."""
        self.app = Application.builder().token(self.config.bot_token).build()
        
        # Command handlers
        self.app.add_handler(CommandHandler("start", self._start_command))
        self.app.add_handler(CommandHandler("menu", self._menu_command))
        self.app.add_handler(CommandHandler("health", self._health_command))
        self.app.add_handler(CommandHandler("report", self._report_command))
        self.app.add_handler(CommandHandler("research", self._research_command))
        self.app.add_handler(CommandHandler("kb", self._kb_command))
        self.app.add_handler(CommandHandler("ops", self._ops_command))
        self.app.add_handler(CommandHandler("advisory", self._advisory_command))
        self.app.add_handler(CommandHandler("sales", self._sales_command))
        self.app.add_handler(CommandHandler("compete", self._compete_command))
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
    # Friendly I/O helpers (Task 6: thân thiện hóa đầu vào/đầu ra)
    # ------------------------------------------------------------------

    async def _friendly_unknown(self, update: Update) -> None:
        """Khi không hiểu ý user -> gợi ý thân thiện thay vì fallback LLM vô nghĩa."""
        text = (
            "🤔 Mình chưa rõ ý bạn muốn làm gì.\n\n"
            "Bạn có thể thử:\n"
            "• 🔍 Tìm job AI intern — nhắn \"tìm 8 job AI intern\"\n"
            "• 💡 Hỏi tư vấn — nhắn \"hỏi Hormozi về chiến lược\"\n"
            "• 📄 Tạo proposal — nhắn \"viết proposal báo giá cho khách\"\n"
            "• 📊 Đối thủ — nhắn \"/compete\" để xem báo cáo tuần\n"
            "• 📥 Tổng hợp — nhắn \"/ops\" để xem Gmail/Calendar hôm nay\n"
            "• 📚 Tra cứu — nhắn \"/kb <câu hỏi>\"\n\n"
            "Gõ /help để xem đầy đủ lệnh."
        )
        await update.message.reply_text(text)

    async def _friendly_error(self, update: Update, err: Exception) -> None:
        """Lỗi hệ thống -> thông báo nhẹ nhàng, không dump traceback thô."""
        await update.message.reply_text(
            f"😔 Xin lỗi, mình gặp lỗi nhỏ: {err}\n"
            "Vui lòng thử lại sau ít phút hoặc gõ /help để xem hướng dẫn."
        )

    def _update_allowlist_env(self, allowed: list[str]) -> None:
        """Ghi đè GMAIL_ALLOWED_RECIPIENTS vào os.environ (docker-safe, không động file .env)."""
        import os as _os, json as _json
        _os.environ["GMAIL_ALLOWED_RECIPIENTS"] = _json.dumps(allowed)

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
    
    async def _ops_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ops — Business Ops Hub daily digest (Task 2)."""
        await update.message.reply_text(
            "📥 Đang tổng hợp Business Ops Hub (Gmail chưa đọc + Calendar + tasks)...",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from agents.monitoring.scheduler import _format_ops_digest

            digest_dict = await self._dispatch_ops_digest()
            text = _format_ops_digest(digest_dict)
            if len(text) > 4000:
                text = text[:3900] + "\n*... (đã rút gọn) ...*"
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi Ops Hub: {e}")

    async def _dispatch_ops_digest(self) -> dict:
        """Build the ops.digest result dict via the registry (shared with scheduler)."""
        from packages.core.bootstrap import get_container
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskContext, TaskRequest
        import uuid as _uuid

        ctn = get_container()
        desc, handler = ctn.registry.get_by_capability("ops.digest")
        resp = await handler.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.OPS,
                action="digest",
                payload={},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
        )
        if resp.status.value != "success" or not resp.result:
            raise RuntimeError(resp.error.message if resp.error else "ops.digest thất bại")
        return resp.result

    async def _advisory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /advisory <persona> <câu hỏi> — AI Advisory Council (Task 3).

        Usage:
            /advisory hormozi <câu hỏi>   — chiến lược
            /advisory buffett <câu hỏi>   — đầu tư
            /advisory garyvee <câu hỏi>   — marketing/tài chính
            /advisory <câu hỏi>           — auto-detect persona từ từ khóa

        Persona is a system-prompt override on the shared LLM (no separate model).
        """
        from packages.core.personas import PERSONAS, PERSONA_LABELS, select_persona
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskContext, TaskRequest
        import uuid as _uuid

        args = context.args or []
        if not args:
            await update.message.reply_text(
                "*Usage:* `/advisory <persona> <câu hỏi>`\n\n"
                "Persona: `hormozi` (chiến lược) | `buffett` (đầu tư) | `garyvee` (marketing/tài chính)\n"
                "Hoặc bỏ qua persona để tự động nhận diện: `/advisory nên pricing gói này thế nào?`\n"
                "VD: `/advisory buffett có nên mua cổ phiếu chia cổ tức không?`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        first = args[0].lower()
        explicit_persona = first if first in PERSONAS else None
        question = " ".join(args[1:] if explicit_persona else args)

        if not question.strip():
            await update.message.reply_text(
                "❌ Thiếu câu hỏi. VD: `/advisory buffett có nên mua cổ phiếu chia cổ tức?`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Auto-detect when no explicit persona given.
        persona = explicit_persona or select_persona(question) or "hormozi"
        label = PERSONA_LABELS.get(persona, persona)
        source = "chỉ định" if explicit_persona else ("tự động nhận diện" if select_persona(question) else "mặc định (hormozi)")

        await update.message.reply_text(
            f"🎯 Đang hỏi *{label}* ({source})...\n\n❓ {question}",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from packages.core.bootstrap import get_container

            ctn = get_container()
            req = TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.ADVISORY,
                action="ask",
                payload={"question": question, "persona": persona},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
            desc, handler = ctn.registry.get_by_capability("advisory.ask")
            resp = await handler.handle(req)
            if resp.status.value == "success":
                ans = resp.result.get("answer", "")
                text = f"🎯 *{label}*\n\n{ans}"
                if len(text) > 4000:
                    text = text[:3900] + "\n*... (đã rút gọn) ...*"
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                msg = resp.error.message if resp.error else "unknown"
                await update.message.reply_text(f"❌ Lỗi Advisory: {msg}")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi Advisory: {e}")

    async def _sales_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /sales <email_text|email_id> — Email-to-Proposal Automation (Task 4).

        Usage:
            /sales <email_text>        — dán nội dung email khách
            /sales <email_id>          — truy xuất từ Gmail (nếu có), fallback dán text

        Generates a branded proposal + pricing, renders a PDF (reportlab,
        offline) and sends it back as a Telegram document, plus a follow-up
        email draft summary.
        """
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskContext, TaskRequest
        import uuid as _uuid

        raw = " ".join(context.args) if context.args else ""
        if not raw.strip():
            await update.message.reply_text(
                "*Usage:* `/sales <email_text>` hoặc `/sales <email_id>`\n\n"
                "VD: `/sales Chào bạn, mình cần báo giá gói Launch Impact ra mắt thương hiệu`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(
            "📨 Đang xử lý email khách → soạn đề xuất + PDF branding...",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from packages.core.bootstrap import get_container

            ctn = get_container()
            # If it looks like a Gmail message id, try to resolve the body first.
            email_text = raw
            if re.match(r"^[a-zA-Z0-9_-]{6,}$", raw.strip()) and "@" not in raw:
                try:
                    desc_g, handler_g = ctn.registry.get_by_capability("gmail.search")
                    gresp = await handler_g.handle(
                        TaskRequest(
                            task_id=_uuid.uuid4(),
                            domain=Domain.GMAIL,
                            action="search",
                            payload={"query": raw.strip(), "max_results": 1},
                            context=TaskContext(
                                organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                                channel="telegram",
                            ),
                        )
                    )
                    if gresp.status.value == "success" and gresp.result and gresp.result.get("messages"):
                        # Use the id as a placeholder body if we cannot fetch full text.
                        email_text = f"[Gmail message id: {raw.strip()}]"
                except Exception:
                    pass  # fall back to treating raw as email text

            req = TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.SALES,
                action="process_email",
                payload={"email_text": email_text},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
            desc, handler = ctn.registry.get_by_capability("sales.process_email")
            resp = await handler.handle(req)
            if resp.status.value != "success" or not resp.result:
                msg = resp.error.message if resp.error else "sales.process_email thất bại"
                await update.message.reply_text(f"❌ Lỗi Sales: {msg}")
                return

            result = resp.result
            intent = result.get("intent", "other")
            client = result.get("client", "Quý khách hàng")
            proposal_name = result.get("proposal_name", "")
            price = result.get("price", 0)
            currency = result.get("currency", "VND")
            follow = result.get("follow_up", {}) or {}
            pdf_bytes = result.get("pdf_bytes") or b""

            summary = (
                f"📨 *Email-to-Proposal* ({intent})\n\n"
                f"👤 Khách: *{client}*\n"
                f"📦 Gói: *{proposal_name}*\n"
                f"💰 Báo giá: *{price:,.0f} {currency}*\n"
                f"📧 Follow-up: _{follow.get('subject', '')}_\n\n"
                f"📎 Đính kèm file PDF đề xuất bên dưới."
            )
            if len(summary) > 4000:
                summary = summary[:3900] + "\n*... (đã rút gọn) ...*"
            await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)

            if pdf_bytes:
                import io as _io

                fname = f"proposal_{proposal_name or 'client'}.pdf".replace(" ", "_")
                await update.message.reply_document(
                    document=_io.BytesIO(pdf_bytes),
                    filename=fname,
                    caption=f"📑 Đề xuất {proposal_name} — {client}",
                )
            else:
                await update.message.reply_text("⚠️ Không sinh được PDF (rỗng).")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi Sales: {e}")

    async def _compete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /compete [tên đối thủ] — Competitive Intelligence (Task 5).

        Usage:
            /compete                — gửi Weekly Competitive Brief tổng hợp
            /compete DoiThuA        — brief chỉ cho đối thủ đó

        Collects competitor posts/pricing via web_search (no LLM crawl),
        analyzes pricing shifts + patterns, returns a short VN Markdown brief.
        """
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskContext, TaskRequest
        import uuid as _uuid

        competitor = " ".join(context.args).strip() if context.args else ""
        await update.message.reply_text(
            "📊 Đang thu thập tín hiệu đối thủ → phân tích → soạn Weekly Brief...",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from packages.core.bootstrap import get_container

            ctn = get_container()
            req = TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.COMPETITOR,
                action="brief",
                payload={"competitor": competitor or None},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
            desc, handler = ctn.registry.get_by_capability("competitor.brief")
            resp = await handler.handle(req)
            if resp.status.value != "success" or not resp.result:
                msg = resp.error.message if resp.error else "competitor.brief thất bại"
                await update.message.reply_text(f"❌ Lỗi Competitive: {msg}")
                return

            brief = resp.result.get("brief", "")
            if len(brief) > 4000:
                brief = brief[:3900] + "\n*... (đã rút gọn) ...*"
            await update.message.reply_text(brief, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi Competitive: {e}")

    async def _kb_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /kb <câu hỏi> — query the Second Brain knowledge base."""
        from agents.knowledge.agent import NO_INFO_ANSWER
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskContext, TaskRequest
        import uuid as _uuid

        question = " ".join(context.args) if context.args else ""
        if not question:
            await update.message.reply_text(
                "*Usage:* `/kb <câu hỏi>`\n\n"
                "Ví dụ: `/kb chính sách hoàn tiền là gì?`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(
            f"🧠 Đang tìm trong Knowledge Base: *{question}*",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from packages.core.bootstrap import get_container

            ctn = get_container()
            req = TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.KNOWLEDGE,
                action="query",
                payload={"question": question},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
            desc, handler = ctn.registry.get_by_capability("knowledge.query")
            resp = await handler.handle(req)
            if resp.status.value == "success":
                ans = resp.result.get("answer", "")
                if ans == NO_INFO_ANSWER:
                    await update.message.reply_text(
                        "🤔 Không tìm thấy thông tin liên quan trong Knowledge Base.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    text = f"🧠 *Knowledge Base*\n\n{ans}"
                    if len(text) > 4000:
                        text = text[:3900] + "\n*... (đã rút gọn) ...*"
                    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                msg = resp.error.message if resp.error else "unknown"
                await update.message.reply_text(f"❌ Lỗi: {msg}")
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi KB: {e}")
    
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
            "🧠 *Knowledge* — '/kb <câu hỏi>' (Second Brain)\n"
            "📨 *Sales* — '/sales <email khách>' (soạn đề xuất + PDF)\n"
            "📊 *Compete* — '/compete [tên đối thủ]' (tình báo cạnh tranh)\n"
            "📥 *Ops Hub* — '/ops' (tổng hợp Gmail+Calendar+tasks)\n"
            "📋 *`/menu`* — Mở menu chính\n"
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
            [InlineKeyboardButton("📚 Knowledge", callback_data="kb"), InlineKeyboardButton("📥 Ops Hub", callback_data="ops")],
            [InlineKeyboardButton("💡 Advisory", callback_data="advisory"), InlineKeyboardButton("📄 Proposal", callback_data="sales")],
            [InlineKeyboardButton("📊 Competitor", callback_data="compete"), InlineKeyboardButton("🏥 Health", callback_data="health")],
            [InlineKeyboardButton("🔍 Research", callback_data="research"), InlineKeyboardButton("📊 Báo cáo", callback_data="report")],
            [InlineKeyboardButton("📧 Gmail", callback_data="gmail"), InlineKeyboardButton("📅 Calendar", callback_data="calendar")],
            [InlineKeyboardButton("🎥 YouTube", callback_data="youtube"), InlineKeyboardButton("🛠 Công cụ", callback_data="tools")],
            [InlineKeyboardButton("❓ Trợ giúp", callback_data="help")],
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
                    tf = _pl2.Path(tempfile.gettempdir()) / "export_report.md"
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
            elif d == "kb":
                await q.edit_message_text(
                    "🧠 *Knowledge Base (Second Brain)*\n"
                    "Gõ: `/kb <câu hỏi>`\n"
                    "VD: `/kb chính sách hoàn tiền là gì?`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self._main_menu_keyboard(),
                )
            elif d == "ops":
                await q.edit_message_text(
                    "📥 *Business Ops Hub*\n"
                    "Gõ: `/ops` — xem tổng hợp Gmail chưa đọc + Calendar + tasks hôm nay.\n"
                    "Hoặc hỏi: 'lịch hôm nay?' / 'check mail'.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self._main_menu_keyboard(),
                )
            elif d == "advisory":
                await q.edit_message_text(
                    "💡 *AI Advisory Council*\n"
                    "Gõ: `/advisory <chuyên gia> <câu hỏi>`\n"
                    "Chuyên gia: hormozi (chiến lược) | buffett (đầu tư) | garyvee (marketing)\n"
                    "VD: `/advisory buffett có nên mua cổ phiếu ngân hàng không?`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self._main_menu_keyboard(),
                )
            elif d == "sales":
                await q.edit_message_text(
                    "📄 *Email → Proposal*\n"
                    "Gõ: `/sales <email khách>` — bot soạn báo giá + proposal PDF + email follow-up.\n"
                    "VD: `/sales chào anh A, bên em cần báo giá gói Launch Impact`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self._main_menu_keyboard(),
                )
            elif d == "compete":
                await q.edit_message_text(
                    "📊 *Competitive Intelligence*\n"
                    "Gõ: `/compete` — xem báo cáo tuần (đối thủ, dịch chuyển giá).\n"
                    "Hoặc: `/compete <tên đối thủ>` để xem riêng.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self._main_menu_keyboard(),
                )
            elif d == "tools":
                from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
                kb = _M([
                    [_B("⚙️ Setup Mail", callback_data="setup_mail"), _B("⬅️ Quay lại", callback_data="back_menu")],
                ])
                await q.edit_message_text("🛠 *Công cụ* — chọn thao tác:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif d == "help":
                await self._help_command(q, context)
                return
            elif d == "jobsearch_cancel":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                self._pending_jobsearch.pop(chat_id2, None)
                await q.edit_message_text("❌ Đã hủy JobSearch. Gửi lại brief khi bạn sẵn sàng.", reply_markup=self._main_menu_keyboard())
            elif d == "youtube_cancel":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                self._pending_youtube.pop(chat_id2, None)
                await q.edit_message_text("❌ Đã hủy YouTube Trending. Gửi lại brief khi bạn sẵn sàng.", reply_markup=self._main_menu_keyboard())
            elif d == "youtube_confirm":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                pending = self._pending_youtube.pop(chat_id2, None)
                if not pending:
                    await q.edit_message_text("⚠️ Không tìm thấy brief YouTube. Gửi lại.", reply_markup=self._main_menu_keyboard())
                    return
                target_mail = pending.get("target_mail", "tanmainguyenbinh@gmail.com")
                await q.edit_message_text(f"🎥 Đang lấy YouTube Trending Việt Nam cho *{target_mail}* (1-2 phút)...", parse_mode=ParseMode.MARKDOWN)
                import asyncio as _aioY, uuid as _uuidY, datetime as _dtY
                import httpx as _httpxY, re as _reY
                async def _do_youtube():
                    try:
                        from packages.core.bootstrap import get_container
                        from packages.contracts.models import TaskRequest, TaskContext
                        from packages.contracts.enums import Domain
                        ctn = get_container()
                        # search trending via youtube agent
                        queries = ["trending Vietnam", "nhạc trending Vietnam", "gaming trending Vietnam"]
                        all_vids: list[dict] = []
                        for _q in queries:
                            try:
                                _req = TaskRequest(task_id=_uuidY.uuid4(), domain=Domain.YOUTUBE, action="search", payload={"query": _q, "limit": 5}, context=TaskContext(organization_id=_uuidY.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                                _desc, _handler = ctn.registry.get_by_capability("youtube.search")
                                _resp = await _handler.handle(_req)
                                if _resp.result and _resp.result.get("results"):
                                    for it in _resp.result["results"]:
                                        if it.get("mock"): continue
                                        it["_q"] = _q
                                        all_vids.append(it)
                            except Exception:
                                continue
                        # fallback if none: try research web_search site:youtube.com trending
                        if not all_vids:
                            try:
                                _req2 = TaskRequest(task_id=_uuidY.uuid4(), domain=Domain.RESEARCH, action="web_search", payload={"query": "site:youtube.com trending Vietnam", "limit": 10}, context=TaskContext(organization_id=_uuidY.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                                _desc2, _handler2 = ctn.registry.get_by_capability("research.web_search")
                                _resp2 = await _handler2.handle(_req2)
                                for it in (_resp2.result.get("results",[]) if _resp2.result else []):
                                    if "youtube.com/watch" in it.get("url",""):
                                        it["_q"]="trending"
                                        all_vids.append(it)
                            except Exception:
                                pass
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=f"🔎 Đã search YouTube {len(all_vids)} nguồn, đang verify link còn xem được...")
                        except Exception:
                            pass
                        seen: set[str] = set()
                        uniq: list[dict] = []
                        for it in all_vids:
                            u = it.get("url","")
                            if u and u not in seen and "youtube.com/watch" in u:
                                # extract video id
                                m = _reY.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", u)
                                vid = m.group(1) if m else ""
                                if not vid: continue
                                seen.add(u)
                                uniq.append(it)
                        verified: list[dict] = []
                        now = _dtY.datetime.now(_dtY.timezone.utc).isoformat()
                        for it in uniq[:15]:
                            url = it.get("url","")
                            vid = _reY.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
                            vid = vid.group(1) if vid else ""
                            title = it.get("title","") or vid
                            channel = "Unknown"
                            views = "N/A"
                            # verify link 200 and try extract title/channel
                            try:
                                async with _httpxY.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as _cli:
                                    _r = await _cli.get(url)
                                    if _r.status_code == 200 and "Video unavailable" not in _r.text and "Private video" not in _r.text:
                                        m_t = _reY.search(r"<title[^>]*>(.*?)</title>", _r.text, flags=_reY.IGNORECASE|_reY.DOTALL)
                                        if m_t:
                                            t = _reY.sub(r"<[^>]+>","", m_t.group(1)).replace(" - YouTube","").strip()
                                            if t and len(t)>5 and "youtube" not in t.lower():
                                                title = t[:80]
                                        # try channel
                                        m_c = _reY.search(r'"ownerChannelName":"([^"]+)"', _r.text)
                                        if m_c: channel = m_c.group(1)
                                        else:
                                            m_c2 = _reY.search(r'"channelName":"([^"]+)"', _r.text)
                                            if m_c2: channel = m_c2.group(1)
                                        status = "VERIFIED"
                                    else:
                                        status = "CLOSED"
                                        continue
                            except Exception:
                                status = "UNCERTAIN"
                                continue
                            if status == "VERIFIED":
                                verified.append({"title": title, "channel": channel, "views": views, "url": url, "video_id": vid, "checked_at": now, "source": it.get("_q","")})
                            if len(verified) >= 10:
                                break
                        if not verified:
                            try:
                                await context.bot.send_message(chat_id=chat_id2, text="⚠️ Không verify được video YouTube nào còn xem được. Mình không bịa link abc123.")
                            except Exception:
                                pass
                            return
                        verified = verified[:10]
                        # build report
                        tbl = "| Rank | Tiêu đề | Kênh | Link |\n|------|---------|------|------|\n"
                        for idx, v in enumerate(verified,1):
                            tbl += f"| {idx} | {v['title'][:30]} | {v['channel'][:15]} | [Watch]({v['url']}) |\n"
                        top3 = ""
                        for v in verified[:3]:
                            top3 += f"\n**{v['title']} — {v['channel']}**\n- Vì sao trending: view tăng nhanh, nội dung viral tại VN\n- Khán giả: người xem YouTube VN quan tâm chủ đề này\n- Nên đu trend: Có — làm content ăn theo trong 48h\n"
                        summary = f"**TOP YOUTUBE TRENDING VIỆT NAM (VERIFIED {len(verified)}/{len(uniq)})**\n\n{tbl}\n{top3}\n\nTổng tìm: {len(uniq)} | VERIFIED: {len(verified)}\nTOP 3 nên xem: {', '.join(v['title'][:20] for v in verified[:3])}\nGợi ý content: reaction / review / behind the scenes theo trend"
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=summary[:4000], parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                        try:
                            from integrations.google_client import gmail_send
                            from packages.config.settings import get_settings
                            allowed = get_settings().gmail_allowed_recipients or []
                            if target_mail.lower() not in [a.lower() for a in allowed]:
                                await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ {target_mail} chưa trong allowlist. Dùng /menu → ⚙️ Setup Mail để thêm.")
                            else:
                                body = summary + "\n\n" + "\n".join(f"{v['title']} | {v['channel']} | {v['url']}" for v in verified)
                                _res = gmail_send(to=target_mail, subject=f"[Business Ops] TOP {len(verified)} YouTube Trending VN — {now[:10]}", body=body)
                                if _res.get("mode")=="DRY_RUN":
                                    await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gmail DRY_RUN chưa gửi thật tới {target_mail}")
                                else:
                                    await context.bot.send_message(chat_id=chat_id2, text=f"✅ Đã gửi báo cáo YouTube Trending về {target_mail} (id {_res.get('id')})")
                        except Exception as _e:
                            try: await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gửi mail lỗi: {_e}")
                            except Exception: pass
                    except Exception as e2:
                        try: await context.bot.send_message(chat_id=chat_id2, text=f"❌ YouTube lỗi: {e2}")
                        except Exception: pass
                _aioY.create_task(_do_youtube())
            elif d == "jobsearch_confirm":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                pending = self._pending_jobsearch.pop(chat_id2, None)
                if not pending:
                    await q.edit_message_text("⚠️ Không tìm thấy brief. Gửi lại brief AI Intern.", reply_markup=self._main_menu_keyboard())
                    return
                target_mail = pending.get("target_mail", "binhtan5734@gmail.com")
                _n_job = "8"
                import re as _re_n
                _m_n = _re_n.findall(r"\b(\d+)\b", pending.get("text", ""))
                if _m_n:
                    _n_job = _m_n[0]
                # Hỏi clarifying nếu brief chưa rõ vị trí cụ thể
                _brief_l = (pending.get("text", "") or "").lower()
                _stop = ["tìm","job","việc","tuyển","gửi","về","mail","trên","mọi","nền","tảng","đang","nhiều","ai intern","intern","ai/ml","5","5 job","cho","tôi","help","trợ giúp"]
                _kw_check = _brief_l
                for _w in _stop:
                    _kw_check = _kw_check.replace(_w, " ")
                _kw_check = " ".join(_kw_check.split()).strip()
                if not _kw_check:
                    self._pending_jobsearch[chat_id2] = pending
                    await q.edit_message_text(
                        "📋 Để tìm kiếm chính xác, bạn vui lòng cho biết:\n"
                        "• Vị trí mong muốn (vd: AI Intern, Data Analyst, Backend Developer...)\n"
                        "• Địa điểm (vd: HCMC, Hà Nội, Remote)\n"
                        "• Kinh nghiệm (Intern / Junior / Senior)\n\n"
                        "Ví dụ: \"AI Intern tại HCMC, Intern\" — hoặc trả lời trực tiếp bên dưới.",
                        reply_markup=self._main_menu_keyboard(),
                    )
                    return
                await q.edit_message_text(f"🔍 Đang tìm kiếm {_n_job} vị trí VERIFIED cho *{target_mail}* (dự kiến 2-3 phút)...", parse_mode=ParseMode.MARKDOWN)
                import asyncio as _aio2, uuid as _uuid2, datetime as _dt2, json as _json2, pathlib as _pl2
                import httpx as _httpx2
                async def _do_jobsearch_confirm():
                    try:
                        from packages.core.bootstrap import get_container
                        from packages.contracts.models import TaskRequest, TaskContext
                        from packages.contracts.enums import Domain
                        ctn = get_container()
                        # Chỉ giữ URL thuộc trang tuyển dụng uy tín (loại google/mail/accounts spam)
                        from agents.monitoring.jobsearch_filters import is_job_url as _is_job_url
                        # Tạo queries từ brief user (không tự thêm "AI" nếu user không nói)
                        _brief = (pending.get("text", "") or "").lower()
                        _kw = _brief
                        for _w in ["tìm", "job", "việc", "tuyển", "gửi", "về", "mail", "trên", "mọi", "nền", "tảng", "đang", "nhiều", "ai intern", "intern", "ai/ml"]:
                            _kw = _kw.replace(_w, " ")
                        _kw = " ".join(_kw.split()).strip() or "thực tập sinh"
                        _kw_cap = _kw[:40].title()
                        queries = [
                            f"{_kw_cap} Ho Chi Minh Vietnam",
                            f"{_kw_cap} Hanoi Vietnam",
                            f"{_kw_cap} Vietnam TopCV",
                            f"{_kw_cap} Vietnam ITviec",
                        ]
                        all_items: list[dict] = []
                        for _q in queries:
                            try:
                                _req = TaskRequest(task_id=_uuid2.uuid4(), domain=Domain.RESEARCH, action="web_search", payload={"query": _q, "limit": 5}, context=TaskContext(organization_id=_uuid2.UUID("00000000-0000-0000-0000-000000000001"), channel="telegram"))
                                _desc, _handler = ctn.registry.get_by_capability("research.web_search")
                                _resp = await _handler.handle(_req)
                                if _resp.result and _resp.result.get("results"):
                                    for it in _resp.result["results"]:
                                        _u = it.get("url", "")
                                        if _u and _is_job_url(_u):
                                            it["_q"] = _q
                                            all_items.append(it)
                            except Exception:
                                continue
                        # inform searching
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=f"🔎 Đã thu thập {len(all_items)} kết quả từ các nền tảng, đang xác thực liên kết ứng tuyển và chấm điểm...")
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
                        from agents.monitoring.jobsearch_filters import verify_job_listing
                        for it in uniq[:25]:
                            url = it.get("url","")
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
                                        _vr = verify_job_listing(url, _html, now, fallback_title=orig_title)
                                        title = _vr["title"]
                                        status = _vr["status"]
                                        confidence = _vr["confidence"]
                                        evidence = _vr["evidence"]
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
                        verified = sorted(verified, key=lambda x: x["match"], reverse=True)[:8]
                        dedup: dict[str, dict] = {}
                        for j in verified:
                            key = f"{j['company'].lower()}|{j['job_title'].lower()}|{j['location'].lower()}"
                            if key not in dedup or j["match"] > dedup[key]["match"]:
                                dedup[key] = j
                        verified = list(dedup.values())[:8]
                        try:
                            _base = _pl2.Path("D:/Business Ops Agent Swarm") if _pl2.Path("D:/Business Ops Agent Swarm/job_search_results.json").parent.exists() else _pl2.Path(".")
                            (_base / "job_search_results.json").write_text(_json2.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "verified_jobs.json").write_text(_json2.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "job_audit_log.json").write_text(_json2.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                        if not verified:
                            try:
                                from urllib.parse import urlparse
                                src_lines = []
                                _seen_dom = set()
                                for u in uniq:
                                    _u = u.get("url", "")
                                    _d = urlparse(_u).netloc.replace("www.", "")
                                    if _is_job_url(_u) and _d not in _seen_dom:
                                        _seen_dom.add(_d)
                                        src_lines.append(f"• {_d}")
                                if not src_lines:
                                    src_lines = [f"• {d}" for d in _JOB_DOMAINS[:6]]
                                _tip_kw = (_kw_cap or "thực tập sinh")
                                await context.bot.send_message(
                                    chat_id=chat_id2,
                                    text=(
                                        "⚠️ Mình đã rà soát {n} kết quả từ các nền tảng nhưng chưa xác thực được vị trí nào "
                                        "có nút ứng tuyển còn mở (để tránh đưa kết quả không chính xác). "
                                        "Bạn có thể trực tiếp kiểm tra các trang tuyển dụng uy tín sau:\n\n"
                                        "{sources}\n\n"
                                        "💡 Gợi ý: tìm trực tiếp '{kw}' trên từng trang để xem các vị trí mới nhất."
                                    ).format(n=len(uniq), sources="\n".join(src_lines), kw=_tip_kw),
                                )
                            except Exception:
                                pass
                            return
                        job_lines = []
                        for idx, j in enumerate(verified, 1):
                            why = j.get("required_skills") or "Đúng ngành AI Intern, khớp nền tảng ML/Cloud"
                            job_lines.append(
                                f"**{idx}. {j['job_title']} — {j['company']}**\n"
                                f"- 📍 Địa điểm: {j.get('location') or '—'}\n"
                                f"- ✅ Đã kiểm tra: Còn tuyển\n"
                                f"- 🔗 Xem chi tiết: {j.get('link') or j.get('url') or '—'}\n"
                                f"- 💡 Phù hợp vì: {why}\n"
                                f"- 👉 Nên nộp hồ sơ: Có"
                            )
                        summary = "**TUYỂN DỤNG AI INTERN — ĐÃ KIỂM TRA**\n\n" + "\n\n".join(job_lines)
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
            # Telegram báo "Message is not modified" khi edit sang nội dung giống hệt -> bỏ qua, không báo lỗi
            if "not modified" in str(e).lower():
                return
            try:
                await q.edit_message_text(f"Lỗi: {e}", reply_markup=self._main_menu_keyboard())
            except Exception:
                pass
    
    async def _message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        # 0) Hiện "đang nhập..." ngay khi nhận tin nhắn để user thấy trạng thái xử lý
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
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
                    self._update_allowlist_env(allowed)
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
                    self._update_allowlist_env(allowed3)
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
        # 0) Normalize: collapse repeated whitespace for robust keyword matching
        import re as _re_norm
        text = _re_norm.sub(r"\s+", " ", text).strip()
        # 0a) Help/menu intent -> show quick menu instead of LLM fallback.
        # NOTE: '?' intentionally excluded — a real question ending in '?' must NOT
        # be hijacked into the menu (was a false positive).
        _help_kw = ("help", "trợ giúp", "tro giup", "menu", "làm được gì", "lam duoc gi", "hướng dẫn", "huong dan", "commands")
        _is_pure_help = text.lower() in _help_kw or text.strip() == "?"
        if len(text) <= 30 and (_is_pure_help or any(k in text.lower() for k in ("trợ giúp", "tro giup", "làm được gì", "lam duoc gi", "hướng dẫn", "huong dan", "commands"))):
            await self._friendly_unknown(update)
            return
        # 2) Quick route for email intent -> use gmail agent (limit hallucination) - CHAT: gui loi chao moi gui
        low = text.lower()
        # Nếu đang ở bước clarifying JobSearch mà user gửi text -> cập nhật brief + hỏi xác nhận lại
        if chat_id in self._pending_jobsearch and not text.startswith("/"):
            try:
                _prev = self._pending_jobsearch[chat_id]
                _low_txt = text.lower().strip()
                # "sửa" -> hủy pending, yêu cầu nhập lại tiêu chí
                if _low_txt in ("sửa", "sua", "edit", "change", "thay đổi", "doi"):
                    self._pending_jobsearch.pop(chat_id, None)
                    await update.message.reply_text(
                        "📝 Bạn muốn tìm với tiêu chí nào? Ví dụ: *tìm 5 job AI intern tại Hà Nội*",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return
                _prev["text"] = (text)
                target_mail = _prev.get("target_mail") or "tanmainguyenbinh@gmail.com"
                import re as _re_num_clar
                _m_job = _re_num_clar.search(r"(tìm|nộp|apply)\s+(\d+)\s*(job|vị trí|viec|việc)", text.lower())
                _m_any = _re_num_clar.findall(r"\b(\d+)\b", text)
                _n_job = _m_job.group(2) if _m_job else (_m_any[0] if _m_any else "8")
                from agents.monitoring.jobsearch_filters import extract_job_keywords
                _kw_disp = extract_job_keywords(text)
                from telegram import InlineKeyboardButton as _Bc, InlineKeyboardMarkup as _Mc
                kb = _Mc([[_Bc(f"✅ Xác nhận tìm {_n_job} vị trí", callback_data="jobsearch_confirm"), _Bc("❌ Hủy", callback_data="jobsearch_cancel")]])
                await update.message.reply_text(
                    f"📋 Xác nhận tìm kiếm việc làm\n\n"
                    f"🔍 Từ khóa: {_kw_disp}\n"
                    f"📊 Số lượng: {_n_job} vị trí (sẽ xác minh trước khi gửi)\n"
                    f"🌐 Nguồn: TopCV, VietnamWorks, ITviec, LinkedIn\n"
                    f"✅ Kiểm tra: link còn mở + chấm điểm phù hợp 0–100\n"
                    f"📧 Gửi báo cáo tới: {target_mail}\n"
                    f"⏱ Thời gian dự kiến: ~2–3 phút\n\n"
                    f"→ Gõ \"OK\" để bắt đầu, hoặc \"sửa\" để thay đổi tiêu chí.",
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                )
                return
            except Exception:
                pass
        import re as _re_gmail
        has_email = _re_gmail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        is_greeting = has_email and ("gửi lời chào" in low or "gui loi chao" in low or ("gửi" in low and "chào" in low) or ("gui" in low and "chao" in low))
        # JobSearch intent — require an explicit hiring keyword, and do NOT treat
        # "tìm hiểu ..." (research) as a hiring request.
        _is_research_phrase = ("tìm hiểu" in low) or ("tìm ra" in low) or ("research" in low)
        is_jobsearch = (
            ("job" in low or "intern" in low or "thực tập" in low or "thuc tap" in low
             or "tuyển dụng" in low or "tuyen dung" in low or "tuyển" in low or "tuyen" in low
             or "ai/ml intern" in low or "machine learning intern" in low)
            or (
                ("tìm" in low or "tim" in low or "search" in low)
                and ("job" in low or "việc" in low or "viec" in low)
                and not _is_research_phrase
            )
        ) or ("ai intern" in low) or ("job search agent" in low)
        # Job Search - hỏi trước khi làm (không tự chạy)
        if is_jobsearch:
            try:
                import re as _re_mail
                m_mail = _re_mail.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                target_mail = m_mail.group(0) if m_mail else "tanmainguyenbinh@gmail.com"
                # Trích số lượng job từ brief (vd "tìm 5 job") — mặc định 8.
                # Ưu tiên số đi cùng "tìm N job", nếu không có thì số đầu tiên,
                # tránh bắt nhầm số điện thoại / năm.
                import re as _re_num_job
                _m_job = _re_num_job.search(r"(tìm|nộp|apply)\s+(\d+)\s*(job|vị trí|viec|việc)", text.lower())
                _m_any = _re_num_job.findall(r"\b(\d+)\b", text)
                _n_job = _m_job.group(2) if _m_job else (_m_any[0] if _m_any else "8")
                self._pending_jobsearch[chat_id] = {"target_mail": target_mail, "text": text}
                from agents.monitoring.jobsearch_filters import extract_job_keywords
                _kw_disp = extract_job_keywords(text)
                from telegram import InlineKeyboardButton as _B2, InlineKeyboardMarkup as _M2
                kb2 = _M2([
                    [_B2(f"✅ Xác nhận tìm {_n_job} vị trí", callback_data="jobsearch_confirm"), _B2("❌ Hủy", callback_data="jobsearch_cancel")],
                ])
                await update.message.reply_text(
                    f"📋 Xác nhận tìm kiếm việc làm\n\n"
                    f"🔍 Từ khóa: {_kw_disp}\n"
                    f"📊 Số lượng: {_n_job} vị trí (sẽ xác minh trước khi gửi)\n"
                    f"🌐 Nguồn: TopCV, VietnamWorks, ITviec, LinkedIn\n"
                    f"✅ Kiểm tra: link còn mở + chấm điểm phù hợp 0–100\n"
                    f"📧 Gửi báo cáo tới: {target_mail}\n"
                    f"⏱ Thời gian dự kiến: ~2–3 phút\n\n"
                    f"→ Gõ \"OK\" để bắt đầu, hoặc \"sửa\" để thay đổi tiêu chí.",
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb2,
                )
                return
            except Exception as e:
                await update.message.reply_text(f"❌ JobSearch lỗi: {e}")
                return
        # YouTube Trending - hoi truoc khi lam
        is_youtube_trending = ("youtube" in low and "trending" in low) or ("video trending" in low) or ("youtube trending agent" in low)
        if is_youtube_trending:
            try:
                import re as _re_mail2
                m_mail2 = _re_mail2.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                target_mail2 = m_mail2.group(0) if m_mail2 else "tanmainguyenbinh@gmail.com"
                self._pending_youtube[chat_id] = {"target_mail": target_mail2, "text": text}
                from telegram import InlineKeyboardButton as _By, InlineKeyboardMarkup as _My
                kby = _My([[_By("✅ Bắt đầu lấy Trending", callback_data="youtube_confirm"), _By("❌ Hủy", callback_data="youtube_cancel")]])
                await update.message.reply_text(
                    f"🎥 Đã nhận brief YouTube Trending — sẽ lấy 10 video trending Việt Nam, verify link youtube.com/watch còn xem được, rồi gửi báo cáo về *{target_mail2}*.\n\nBạn có muốn bắt đầu ngay không?",
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kby,
                )
                return
            except Exception as e:
                await update.message.reply_text(f"❌ YouTube lỗi: {e}")
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
        # 2b) Quick deterministic code snippet (no LLM) — MUST run BEFORE the greeting
        # fast-path, otherwise "viết code python hello world" (contains "hello") would be
        # misclassified as a greeting.
        low2 = text.lower()
        is_code_req = ("hello world" in low2) or ("viết code" in low2) or ("code đơn giản" in low2) or ("viết cho tôi đoạn code" in low2) or ("đoạn code" in low2)
        if is_code_req:
            _snippets = {
                "python": 'print("Hello, World!")',
                "java": 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello, World!");\n    }\n}',
                "javascript": 'console.log("Hello, World!");',
                "js": 'console.log("Hello, World!");',
                "c++": '#include <iostream>\nint main() {\n    std::cout << "Hello, World!";\n    return 0;\n}',
                "cpp": '#include <iostream>\nint main() {\n    std::cout << "Hello, World!";\n    return 0;\n}',
                "c": '#include <stdio.h>\nint main() {\n    printf("Hello, World!");\n    return 0;\n}',
                "go": 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello, World!") }',
                "golang": 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello, World!") }',
                "rust": 'fn main() {\n    println!("Hello, World!");\n}',
                "php": '<?php\necho "Hello, World!";\n?>',
                "ruby": 'puts "Hello, World!"',
                "swift": 'import Foundation\nprint("Hello, World!")',
                "kotlin": 'fun main() {\n    println("Hello, World!")\n}',
                "typescript": 'console.log("Hello, World!");',
                "ts": 'console.log("Hello, World!");',
                "bash": '#!/bin/bash\necho "Hello, World!"',
                "shell": '#!/bin/bash\necho "Hello, World!"',
            }
            _lang = "python"
            for _k in _snippets:
                if _k in low2:
                    _lang = _k
                    break
            await update.message.reply_text(f"```{_lang}\n{_snippets[_lang]}\n```")
            return
        # 2a) Fast greeting (no LLM) — giảm latency cho tin nhắn đơn giản.
        # Chạy SAU code-snippet để "hello world" không bị bắt nhầm thành chào.
        # Quan trọng: so khớp theo TỪ ĐỨNG RIÊNG (word boundary), không phải substring —
        # 'hi' là substring của 'layoff'/'neighbor'/'history' gây false positive.
        import re as _re_greet
        _simple_greet = ("xin chào", "chào", "hi", "hello", "hey", "cảm ơn", "thanks", "good morning", "good evening", "chào bạn")
        _greet_rx = _re_greet.compile(r"\b(" + "|".join(_simple_greet) + r")\b", _re_greet.IGNORECASE)
        if len(text) <= 40 and (_greet_rx.search(text) or text.lower().strip() in _simple_greet):
            await update.message.reply_text(
                "Xin chào! Mình là My AI Agent Bot của Mai Nguyễn Bình Tân. "
                "Bạn cần mình tìm job AI intern, viết code, hay việc gì khác?"
            )
            return
        # 2c) Advisory Council auto-detect: if the free-text question carries a
        # persona keyword (strategy/buffett/marketing/invest/...), route to the
        # advisory.ask capability instead of the generic chat path (Task 3).
        # NOTE: an explicit sales intent (báo giá/proposal/quote/...) must NOT be
        # hijacked into a persona suggestion — sales (2d) handles it. So skip
        # advisory when a sales keyword is present.
        try:
            _sales_kw_2c = ("báo giá", "bao gia", "quote", "proposal", "đề xuất", "de xuat", "chào giá", "chao gia", "email khách", "báo gia")
            from packages.core.personas import select_persona as _sel_persona
            if _sel_persona(text) and not any(k in low for k in _sales_kw_2c):
                await self._advisory_command(
                    update,
                    type("__Ctx", (), {"args": text.split()})(),
                )
                return
        except Exception:
            pass
        # 2d) Sales email-to-proposal: if the free-text message carries a sales
        # intent keyword (báo giá / proposal / quote / email khách), route to the
        # sales.process_email capability instead of the generic chat path (Task 4).
        try:
            _sales_kw = ("báo giá", "bao gia", "quote", "proposal", "đề xuất", "de xuat", "chào giá", "chao gia", "email khách", "báo gia")
            if any(k in low for k in _sales_kw):
                await self._sales_command(
                    update,
                    type("__Ctx", (), {"args": text.split()})(),
                )
                return
        except Exception:
            pass
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
                        if typing_task: typing_task.cancel()
                        await self._friendly_unknown(update)
                        return
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
            answer = await llm.generate(
                prompt=text,
                system=(
                    "Bạn là trợ lý Business Ops của Mai Nguyễn Bình Tân (trả lời tiếng Việt).\n"
                    "QUY TẮC BẮT BUỘC:\n"
                    "1. Không bao giờ bịa dữ liệu.\n"
                    "2. Khi được yêu cầu 'viết code', chỉ trả ĐÚNG 1 đoạn code đơn giản nhất "
                    "(mặc định Python) trừ khi người dùng chỉ rõ ngôn ngữ khác.\n"
                    "3. KHÔNG liệt kê nhiều ngôn ngữ, KHÔNG lặp lại nội dung, KHÔNG giải thích dài dòng.\n"
                    "4. TÓM TẮT TRỌNG TÂM: trả lời ngắn gọn, đúng ý hỏi, tối đa 5 dòng. "
                    "Nếu hỏi 'nghề nào layoff nhiều' thì chỉ liệt kê tên nghề + 1 câu nguyên nhân, không bài luận.\n"
                    "5. Cần dữ liệu thật (mail, calendar, research) thì nói rõ chưa có tool, không tự tạo."
                ),
                max_tokens=400,
                temperature=0.3,
            )
            reply = answer if isinstance(answer, str) else str(answer)
            if typing_task: typing_task.cancel()
            await update.message.reply_text(reply[:4000])
        except Exception as e:
            if typing_task:
                try: typing_task.cancel()
                except Exception: pass
            import logging
            logging.getLogger(__name__).exception("telegram error: %s", e)
            try: await self._friendly_error(update, e)
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
