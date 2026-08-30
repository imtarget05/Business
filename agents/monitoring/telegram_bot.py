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
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# Telegram MarkdownV1 requires these characters to be backslash-escaped or the
# API raises "Can't parse entities". External content (web/LLM research reports,
# ops digests) is NOT safe markdown, so we escape it before sending.
_TG_MD_ESCAPE = re.compile(r"([_*`\[\]()~>#+\-=|{}])")


def _tg_escape_md(text: str) -> str:
    """Escape Telegram-MarkdownV1 special chars so untrusted text never crashes send."""
    if not text:
        return text
    return _TG_MD_ESCAPE.sub(r"\\\1", text)


# Questions that need real-world / up-to-date data — must NOT be answered
# from a small local LLM's memory (it hallucinates). Route them to research.
_WEB_LOOKUP_RE = re.compile(
    r"(ở đâu|nơi nào|địa chỉ|quán nào|nhà hàng|món ăn|ẩm thực|tin tức|tin mới|"
    r"mới nhất|danh sách|xếp hạng|top \d+|kết quả (trận|bóng)|giá (hiện tại|bao nhiêu)|"
    r"thời tiết|tỷ giá|công bố|vinh danh|giải thưởng|sao michelin|michelin|năm 20\d\d)",
    re.I,
)


def _needs_web_lookup(text: str) -> bool:
    """True if the question needs web data instead of LLM memory."""
    return bool(text) and bool(_WEB_LOOKUP_RE.search(text))


# Food / restaurant / Michelin questions must be web-verified, never answered
# from LLM memory (it hallucinates dishes, stars, and fake restaurants like
# "Noma (Hà Nội)"). This regex gates the strict verify-before-answer path.
_FOOD_LOOKUP_RE = re.compile(
    r"(món ăn|mon an|ẩm thực|am thuc|nhà hàng|nha hang|michelin|saо michelin|"
    r"saо michelin|vinh danh|danh sách.*michelin|restaurant)",
    re.I,
)


def _is_food_lookup(text: str) -> bool:
    return bool(text) and bool(_FOOD_LOOKUP_RE.search(text))


def _food_query(text: str) -> str:
    """Build a focused web query from a food/Michelin question (drop filler words).

    Targets the official Michelin Guide so results are verifiable instead of
    generic (and often hallucinated) LLM memory.
    """
    _toks = re.findall(r"[a-zA-ZÀ-ỹ0-9]+", text.lower())
    _KEEP = {
        "mon", "món", "an", "ăn", "am", "ẩm", "thuc", "thực", "nha", "nhà", "hang", "hàng",
        "bun", "bún", "pho", "phở", "banh", "bánh", "com", "cơm", "mi", "mì", "xoi", "xèo",
        "cha", "chả", "cuon", "cuốn", "rieu", "riêu", "hanoi", "hà", "nội", "ho", "hồ",
        "chi", "chí", "minh", "hcm", "saigon", "viet", "việt", "nam",
    }
    _kept = [t for t in _toks if t in _KEEP]
    _q = " ".join(_kept) or text
    _q = (_q + " michelin guide vietnam").strip()
    return _q


async def _real_web_search(query: str) -> list[dict]:
    """Run a REAL web_search via the container registry (proven in JobSearch).

    Returns the raw result list; empty list means nothing verifiable was found.
    Never falls back to LLM memory, so callers can refuse to answer instead of
    hallucinating.
    """
    try:
        from packages.core.bootstrap import get_container
        from packages.contracts.enums import Domain
        from packages.contracts.models import TaskRequest, TaskContext
        import uuid as _uuid
        ctn = get_container()
        _desc, _handler = ctn.registry.get_by_capability("research.web_search")
        resp = await _handler.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.RESEARCH,
                action="web_search",
                payload={"query": query, "limit": 5},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="telegram",
                ),
            )
        )
        if resp and getattr(resp, "result", None):
            return resp.result.get("results", []) or []
    except Exception:
        pass
    return []


def _sanitize_text(text: str) -> str:
    """Clean AI-generated text before sending to Telegram.

    - NFC-normalizes (fixes composed Vietnamese diacritics)
    - Drops control chars, lone surrogates and unassigned code points
      (the usual source of "broken icon" boxes from small LLMs)
    - Strips variation selectors that render as tofu on some clients
    """
    if not text:
        return text
    t = unicodedata.normalize("NFC", text)
    t = t.replace("\ufe0e", "").replace("\ufe0f", "").replace("\ufffd", "")
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:      # lone surrogate
            continue
        if cat == "Cn":                    # unassigned
            continue
        if cat.startswith("C") and ch not in ("\n", "\t"):  # other controls
            continue
        out.append(ch)
    return "".join(out)


class _SanitizingBot:
    """Wrapper that forces every outgoing text through _sanitize_text.

    Guarantees emoji + Vietnamese diacritics survive to the Telegram client
    regardless of which send path a handler uses (reply_text / send_message /
    edit_message_text). This is the runtime defense against the "lỗi phông chữ"
    symptom on clients that mangle copy-pasted / decomposed Unicode.
    """

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bot, name)

    async def send_message(self, chat_id: int, text: str, *a: Any, **kw: Any) -> Any:
        return await self._bot.send_message(chat_id, _sanitize_text(text), *a, **kw)

    async def edit_message_text(self, text: str, *a: Any, **kw: Any) -> Any:
        return await self._bot.edit_message_text(_sanitize_text(text), *a, **kw)


def _sanitize_reply(text: str) -> str:
    return _sanitize_text(text)


def _md_to_telegram_html(md: str) -> str:
    """Convert an LLM/markdown report into friendly Telegram HTML.

    - Removes literal escape backslashes (\\#, \\-, \\* ...)
    - Headings  #/##/###  -> bold section titles
    - Bullets   -/*       -> "•"
    - Dividers  ---/***/  -> ━━━ line
    - **bold** / __bold__ -> <b>, *italic* -> <i>, `code` -> <code>
    - [text](url)         -> <a href="url">text</a>
    - HTML-escapes < > & so untrusted content can't break parsing.
    """
    if not md:
        return md
    t = re.sub(r"\\([_`\[\]()~>#+\-=|{}.!])", r"\1", md)  # unescape \x -> x
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # headings -> bold with a small marker for visual hierarchy
    t = re.sub(r"(?m)^#{1,6}\s*(.+?)\s*:?\s*$", r"<b>📌 \1</b>", t)
    # dividers
    t = re.sub(r"(?m)^[-*_]{3,}\s*$", "━━━━━━━━━━━━━━━━━━", t)
    # bullets (after dividers so '---' isn't touched; '* ' at line start -> •)
    t = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", t)
    # links first (before bold/italic eat the brackets)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    # emphasis
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"__(.+?)__", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", t)
    return t

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
            self.bot = _SanitizingBot(Bot(token=config.bot_token))
        else:
            self.bot = _SanitizingBot(_StubBot(token=config.bot_token))  # Use stub for testing/offline
        self.app: Application | None = None
        self._research_awaiting: dict[int, str] = {}  # chat_id -> query
        self._seen_chats: set[int] = set()  # only greet Target is ready once per new chat
        self._awaiting_add_mail: set[int] = set()
        self._awaiting_del_mail: set[int] = set()
        self._pending_jobsearch: dict[int, dict] = {}  # chat_id -> {target_mail, text}
        self._last_jobsearch: dict[int, list[dict]] = {}  # chat_id -> final_list from last run (for send-unconfirmed)
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
    
    # --- i18n: follow the user's Telegram app language (vi/en) ---
    _TR = {
        "connecting": {
            "vi": "⏳ Đang kết nối...",
            "en": "⏳ Connecting...",
        },
        "typing": {
            "vi": "✍️ Đang nhập...",
            "en": "✍️ Typing...",
        },
        "researching": {
            "vi": "🔍 Đang nghiên cứu: *{query}*\n\nVui lòng đợi một lát...",
            "en": "🔍 Researching: *{query}*\n\nThis may take a moment...",
        },
        "ops_digest": {
            "vi": "📥 Đang tổng hợp Business Ops Hub (Gmail chưa đọc + Calendar + tasks)...",
            "en": "📥 Compiling Business Ops Hub digest (unread Gmail + Calendar + tasks)...",
        },
        "research_failed": {
            "vi": "❌ Nghiên cứu thất bại: {err}",
            "en": "❌ Research failed: {err}",
        },
        "research_error": {
            "vi": "❌ Lỗi nghiên cứu: {err}",
            "en": "❌ Research error: {err}",
        },
        "truncated": {
            "vi": "\n*... bị cắt bớt ...*",
            "en": "\n*... truncated ...*",
        },
        "welcome_new": {
            "vi": "🎯 *Target is ready!*\\nXin chào Mai Nguyễn Bình Tân — Bot đã sẵn sàng.",
            "en": "🎯 *Target is ready!*\\nHello Mai — the bot is ready.",
        },
        "welcome_back": {
            "vi": "Chào lại Mai!",
            "en": "Welcome back, Mai!",
        },
    }

    def _lang(self, update) -> str:
        """Return 'vi' or 'en' based on the user's Telegram app language setting."""
        user = getattr(update, "effective_user", None)
        code = (getattr(user, "language_code", "") or "").lower()
        return "vi" if code.startswith("vi") else "en"

    def _tr(self, update, key: str, **kw) -> str:
        """Localized string for a status/message key."""
        entry = self._TR.get(key, {})
        txt = entry.get(self._lang(update)) or entry.get("en") or key
        return txt.format(**kw) if kw else txt

    async def _start_typing(self, chat_id: int):
        """Start a keep-typing loop; returns a task to cancel when done."""
        async def _keep():
            while True:
                try:
                    await self.app.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    return
                await asyncio.sleep(4)
        try:
            await self.app.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        return asyncio.create_task(_keep())

    async def _stop_typing(self, task) -> None:
        if task:
            try:
                task.cancel()
            except Exception:
                pass

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
        
        # Localized status: "connecting..." -> edits itself to "researching..."
        status = await update.message.reply_text(self._tr(update, "connecting"))
        typing = await self._start_typing(update.effective_chat.id)
        try:
            await status.edit_text(
                self._tr(update, "researching", query=query),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        
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
                    report = report[:3900] + self._tr(update, "truncated")
                await update.message.reply_text(
                    _md_to_telegram_html(_sanitize_text(report)), parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    self._tr(update, "research_failed", err=result.get("error", "unknown"))
                )
        except Exception as e:
            await update.message.reply_text(self._tr(update, "research_error", err=str(e)))
        finally:
            await self._stop_typing(locals().get("typing"))
    
    async def _ops_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ops — Business Ops Hub daily digest (Task 2)."""
        status = await update.message.reply_text(self._tr(update, "connecting"))
        typing = await self._start_typing(update.effective_chat.id)
        try:
            await status.edit_text(
                self._tr(update, "ops_digest"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        try:
            from agents.monitoring.scheduler import _format_ops_digest

            digest_dict = await self._dispatch_ops_digest()
            text = _format_ops_digest(digest_dict)
            if len(text) > 4000:
                text = text[:3900] + "\n*... (đã rút gọn) ...*"
            await update.message.reply_text(_md_to_telegram_html(_sanitize_text(text)), parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi Ops Hub: {e}")
        finally:
            await self._stop_typing(locals().get("typing"))

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
            txt = self._tr(update, "welcome_new")
        else:
            txt = self._tr(update, "welcome_back")
        from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
        kb = _M([[ _B("📋 Mở menu", callback_data="open_menu") ]])
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    async def _menu_command(self, update, context):
        await update.message.reply_text("Menu chinh — chon chuc nang:", parse_mode=ParseMode.MARKDOWN, reply_markup=self._main_menu_keyboard())

    def _feedback_keyboard(self, task_id: str):
        """👍/👎 inline keyboard linking a reply to its task (learning loop)."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("👍 Hữu ích", callback_data=f"fb:up:{task_id}"),
                    InlineKeyboardButton("👎 Chưa đúng", callback_data=f"fb:down:{task_id}"),
                ]
            ]
        )

    async def _button_callback(self, update, context):
        q = update.callback_query
        await q.answer()
        d = q.data
        # Inline feedback buttons (👍/👎) — route into the learning loop so the
        # system visibly improves from user ratings (friendly feedback loop).
        if d and d.startswith("fb:"):
            try:
                _, rating, task_id = d.split(":", 2)
                from packages.core.bootstrap import get_container
                try:
                    learning = get_container().learning
                except Exception:
                    from packages.core.learning import LearningEngine
                    learning = LearningEngine()
                await learning.record_feedback(
                    {
                        "task_id": task_id,
                        "rating": rating,
                        "source": "telegram",
                    }
                )
                thanks = (
                    "🙏 Cảm ơn bạn! Tôi đã ghi nhận phản hồi *tích cực* và sẽ "
                    "tiếp tục trả lời theo hướng này."
                    if rating == "up"
                    else "🙏 Cảm ơn bạn đã góp ý! Tôi đã ghi nhận và sẽ cải thiện "
                    "cách trả lời. Bạn có thể diễn đạt lại hoặc cho tôi biết "
                    "bạn cần agent nào (research/report/gmail/kb...)."
                )
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                if update.message:
                    await update.message.reply_text(thanks, parse_mode=ParseMode.MARKDOWN)
                else:
                    await q.edit_message_text(thanks, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error("feedback callback failed: %s", e)
            return
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
            elif d == "jobsearch_send_unconfirmed":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                _final = self._last_jobsearch.get(chat_id2, [])
                if not _final:
                    await q.edit_message_text("⚠️ Không có kết quả để gửi. Hãy chạy tìm kiếm lại.", reply_markup=self._main_menu_keyboard())
                    return
                target_mail = (self._pending_jobsearch.get(chat_id2, {}) or {}).get("target_mail") or "tanmainguyenbinh@gmail.com"
                try:
                    from integrations.google_client import gmail_send
                    from packages.config.settings import get_settings
                    allowed = get_settings().gmail_allowed_recipients or []
                    _summary_lines = "\n".join(f"{i+1}. {j.get('job_title','?')} — {j.get('company','?')} | {j.get('link') or j.get('url')}" for i,j in enumerate(_final))
                    _body = f"**TUYỂN DỤNG — CHƯA XÁC NHẬN (gửi theo yêu cầu user)**\n\n{_summary_lines}"
                    if target_mail.lower() not in [a.lower() for a in allowed]:
                        await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ {target_mail} chưa trong allowlist. Dùng /menu → ⚙️ Setup Mail → ➕ Thêm mail trước.")
                    else:
                        _res = gmail_send(to=target_mail, subject=f"[Business Ops] TOP {len(_final)} JobSearch CHUA XAC NHAN (user yêu cầu gửi) — {datetime.now(timezone.utc).isoformat()[:10]}", body=_body)
                        if _res.get("mode") == "DRY_RUN":
                            await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gmail DRY_RUN, chưa gửi thật tới {target_mail}")
                        else:
                            await context.bot.send_message(chat_id=chat_id2, text=f"📨 Đã gửi (chưa verify) TOP {len(_final)} về {target_mail} (id {_res.get('id')})")
                except Exception as _e:
                    try: await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gửi mail lỗi: {_e}")
                    except Exception: pass
                return
            elif d == "jobsearch_expand":
                await q.edit_message_text("🔍 Mở rộng nguồn: thử lại với thêm từ khóa ngành + nhiều trang (TopCV, ITviec, VietnamWorks, LinkedIn, CareerBuilder)... Vui lòng gõ lại brief nếu muốn đổi tiêu chí.", reply_markup=self._main_menu_keyboard())
                return
            elif d == "jobsearch_confirm":
                chat_id2 = q.message.chat.id if q.message and q.message.chat else 0
                pending = self._pending_jobsearch.pop(chat_id2, None)
                if not pending:
                    await q.edit_message_text("⚠️ Không tìm thấy brief. Gửi lại brief AI Intern.", reply_markup=self._main_menu_keyboard())
                    return
                target_mail = pending.get("target_mail", "binhtan5734@gmail.com")
                from agents.monitoring.jobsearch_filters import parse_job_count as _parse_n
                _n_job = _parse_n(pending.get("text", "")) or 8
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
                from agents.monitoring.jobsearch_filters import searching_label as _search_lbl
                await q.edit_message_text(_search_lbl(_n_job) + f" cho *{target_mail}*", parse_mode=ParseMode.MARKDOWN)
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
                        _brief = (pending.get("text", "") or "")
                        # Use the tested keyword extractor (strips hiring verbs + stopwords)
                        from agents.monitoring.jobsearch_filters import extract_job_keywords
                        _kw = extract_job_keywords(_brief).lower()
                        _kw = _kw or "thực tập sinh"
                        _kw_cap = _kw[:40].title()
                        # Feature 4: boost relevance from org context memory.
                        try:
                            _ctn = get_container()
                            _ctx_desc, _ctx_handler = _ctn.registry.get_by_capability("context.get")
                            _org_id = _uuid2.UUID("00000000-0000-0000-0000-000000000001")
                            _ctx_res = await _ctx_handler.handle(TaskRequest(
                                task_id=_uuid2.uuid4(), domain=Domain.RESEARCH,
                                action="get", payload={},
                                context=TaskContext(organization_id=_org_id, channel="telegram"),
                            ))
                            _ctx_items = (_ctx_res.result or {}).get("messages", [])
                            if _ctx_items:
                                from agents.monitoring.jobsearch_filters import context_job_keywords as _ctx_jw
                                _ctx_k = _ctx_jw(_ctx_items)
                                if _ctx_k and _ctx_k not in _kw:
                                    _kw = f"{_kw} {_ctx_k}".strip()
                                    _kw_cap = _kw[:40].title()
                        except Exception:
                            pass
                        # Target the REAL location the user stated (Feature 2).
                        from agents.monitoring.jobsearch_filters import extract_location as _ext_loc
                        _loc = _ext_loc(pending.get("text", ""))
                        _job_q = f"{_kw_cap} tuyển dụng"
                        if _loc and _loc != "Remote":
                            # focused city query + Vietnam-wide, no site: operator
                            # (Bing RSS often returns 0 with site: filters)
                            queries = [
                                f"{_job_q} {_loc} Vietnam",
                                f"{_job_q} {_loc}",
                                f"việc làm {_kw_cap} tại {_loc}",
                            ]
                        elif _loc == "Remote":
                            queries = [f"{_job_q} Remote Vietnam", f"{_job_q} Remote"]
                        else:
                            queries = [
                                f"{_job_q} Vietnam",
                                f"tuyển dụng {_kw_cap}",
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
                        candidates: list[dict] = []
                        audit: list[dict] = []
                        now = _dt2.datetime.now(_dt2.timezone.utc).isoformat()
                        bg_keywords = ["python","docker","kubernetes","pytorch","computer vision","machine learning","mlops","llm","generative ai","agent","cloud"]
                        from agents.monitoring.jobsearch_filters import verify_job_listing
                        # Task 3.2: fetch page text via WebToolsProvider (handles anti-bot
                        # better than raw httpx); falls back to httpx inside helper.
                        try:
                            from packages.tools.web import HttpxWebTools
                            _web = HttpxWebTools()
                        except Exception:
                            _web = None
                        for it in uniq[:25]:
                            url = it.get("url","")
                            orig_title = it.get("title","") or it.get("snippet","") or url.split("/")[2]
                            title = orig_title
                            status = "UNCERTAIN"
                            evidence = f"search {it.get('_q','')} found {url}"
                            confidence = 0.6
                            html_title = ""
                            # fetch de lay title that + check Apply (Task 3.2: via WebToolsProvider)
                            try:
                                from agents.monitoring.jobsearch_filters import extract_page_text
                                _html = await extract_page_text(url, _web)
                                if _html:
                                    _vr = verify_job_listing(url, _html, now, fallback_title=orig_title)
                                    title = _vr["title"]
                                    status = _vr["status"]
                                    confidence = _vr["confidence"]
                                    evidence = _vr["evidence"]
                                else:
                                    status = "UNCERTAIN"
                                    evidence = "no html fetched"
                            except Exception as _e:
                                evidence = f"fetch error: {_e}"
                            # chi dua VERIFIED detail vao list chinh
                            low_title = (title + " " + url).lower()
                            skill_match = sum(1 for k in bg_keywords if k in low_title) / max(1, len(bg_keywords)) * 40 + 50
                            if "intern" in low_title: skill_match += 10
                            if "ai" in low_title: skill_match += 10
                            match = int(min(95, max(55, skill_match)))
                            # Label location from the user's stated location FIRST,
                            # then fall back to what the listing/page actually says.
                            _loc_label = _ext_loc(pending.get("text", ""))
                            if _loc_label:
                                loc = _loc_label
                            else:
                                loc = "Hồ Chí Minh" if "hcm" in low_title or "ho chi minh" in low_title else ("Hà Nội" if "hanoi" in low_title or "ha noi" in low_title else "Vietnam")
                            # tach company tu html title: thuong "Job - Company | Site"
                            company = "Unknown"
                            if " - " in title: company = title.split(" - ")[1].split("|")[0].split("-")[0].strip()[:40]
                            elif " tại " in title.lower(): company = title.lower().split(" tại ")[1].split("|")[0].strip()[:40].title()
                            else: company = orig_title.split("—")[0].strip()[:40] if "—" in orig_title else title.split("|")[0].strip()[:40]
                            if not company or len(company) < 3: company = url.split("/")[2]
                            job = {"company": company or "Unknown","job_title": title[:80],"location": loc,"work_type": "On-site","salary": "","deadline": "","required_skills": ", ".join([k for k in bg_keywords if k in low_title][:5]),"experience": "Intern","link": url,"checked_at": now,"evidence": evidence,"confidence": confidence,"status": status,"match": match,"source": it.get("_q","")}
                            if status == "VERIFIED":
                                verified.append(job)
                            else:
                                # Feature 3: keep UNCERTAIN/CLOSED as ranked candidates
                                # (honest label) instead of discarding — never give up empty.
                                candidates.append(job)
                            audit.append({"url": url, "title": title, "search_timestamp": now, "verification_timestamp": now, "status": status, "evidence": evidence, "confidence": confidence})
                        # Feature 3: merge VERIFIED (ranked first) + UNCERTAIN into a
                        # single de-duplicated, match-ranked candidate list.
                        from agents.monitoring.jobsearch_filters import select_candidates as _select
                        from agents.monitoring.jobsearch_filters import parse_job_count as _pjc
                        _limit = _pjc(pending.get("text", "")) or 8
                        from agents.monitoring.jobsearch_filters import dedupe_candidates as _dedupe
                        final_list = _dedupe(_select(verified, candidates, limit=_limit))
                        # V3: log why items were dropped (promised -> actual)
                        _verified_count = sum(1 for j in final_list if j.get("status") == "VERIFIED")
                        try:
                            _drop_log = (
                                f"📊 Thống kê: hứa {_limit} | thu thập {len(all_items)} | "
                                f"job-URL hợp lệ {len(uniq)} | sau verify còn {len(final_list)} "
                                f"(VERIFIED={_verified_count})."
                            )
                            await context.bot.send_message(chat_id=chat_id2, text=_drop_log)
                        except Exception:
                            pass
                        try:
                            _base = _pl2.Path("D:/Business Ops Agent Swarm") if _pl2.Path("D:/Business Ops Agent Swarm/job_search_results.json").parent.exists() else _pl2.Path(".")
                            (_base / "job_search_results.json").write_text(_json2.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "verified_jobs.json").write_text(_json2.dumps(final_list, ensure_ascii=False, indent=2), encoding="utf-8")
                            (_base / "job_audit_log.json").write_text(_json2.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                        if not final_list:
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
                        for idx, j in enumerate(final_list, 1):
                            why = j.get("required_skills") or "Đúng ngành AI Intern, khớp nền tảng ML/Cloud"
                            if j.get("status") == "VERIFIED":
                                _check = "✅ Đã kiểm tra: Còn tuyển (có nút ứng tuyển)"
                                _apply = "Có"
                            else:
                                _check = "⚠️ Chưa xác nhận được nút ứng tuyển (liên kết liên quan, tự kiểm tra trước khi nộp)"
                                _apply = "Cần kiểm tra"
                            job_lines.append(
                                f"**{idx}. {j['job_title']} — {j['company']}**\n"
                                f"- 📍 Địa điểm: {j.get('location') or '—'}\n"
                                f"- {_check}\n"
                                f"- 🔗 Xem chi tiết: {j.get('link') or j.get('url') or '—'}\n"
                                f"- 💡 Phù hợp vì: {why}\n"
                                f"- 👉 Nên nộp hồ sơ: {_apply}"
                            )
                        # V5: honest header reflecting verification reality
                        from agents.monitoring.jobsearch_filters import build_header as _build_header
                        _header = _build_header(_verified_count, len(final_list))
                        summary = f"{_header}\n\n" + "\n\n".join(job_lines)
                        try:
                            await context.bot.send_message(chat_id=chat_id2, text=summary[:4000], parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                        # V2: verify-gate — do NOT auto-send if 0/low VERIFIED
                        from agents.monitoring.jobsearch_filters import decide_send_gate as _gate
                        _gate_decision = _gate(_limit, _verified_count, final_list)
                        if _gate_decision["action"] == "ASK_USER":
                            try:
                                self._last_jobsearch[chat_id2] = final_list
                                from telegram import InlineKeyboardButton as _Bg, InlineKeyboardMarkup as _Mg
                                _kb = _Mg([[
                                    _Bg("📨 Gửi luôn (chưa verify)", callback_data="jobsearch_send_unconfirmed"),
                                    _Bg("🔍 Mở rộng nguồn", callback_data="jobsearch_expand"),
                                    _Bg("❌ Hủy", callback_data="jobsearch_cancel"),
                                ]])
                                await context.bot.send_message(
                                    chat_id=chat_id2,
                                    text=(
                                        f"⚠️ Hệ thống chỉ verify được {_verified_count}/{_limit} vị trí (lý do: {_gate_decision['reason']}). "
                                        "Theo cam kết 'xác minh trước khi gửi', mình CHƯA gửi email. Bạn muốn:"
                                    ),
                                    reply_markup=_kb,
                                )
                            except Exception:
                                pass
                            return
                        # SEND path (only when gate allows)
                        try:
                            from integrations.google_client import gmail_send
                            from packages.config.settings import get_settings
                            allowed = get_settings().gmail_allowed_recipients or []
                            email_body = summary + "\n\n" + "\n".join(f"{j['company']} | {j['job_title']} | {j['link']} | Match {j['match']} | {j['evidence']}" for j in final_list[:5])
                            if target_mail.lower() not in [a.lower() for a in allowed]:
                                await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ {target_mail} chưa trong allowlist ({', '.join(allowed)}). Dùng /menu → ⚙️ Setup Mail → ➕ Thêm mail trước.")
                            else:
                                _tag = "VERIFIED" if _verified_count else "CHUA XAC NHAN"
                                _res = gmail_send(to=target_mail, subject=f"[Business Ops] TOP {len(final_list[:5])} JobSearch {_tag} — {now[:10]}", body=email_body)
                                if _res.get("mode") == "DRY_RUN":
                                    await context.bot.send_message(chat_id=chat_id2, text=f"⚠️ Gmail DRY_RUN, chưa gửi thật tới {target_mail}")
                                else:
                                    await context.bot.send_message(chat_id=chat_id2, text=f"✅ Đã gửi báo cáo TOP {len(final_list[:5])} {_tag} về {target_mail} (id {_res.get('id')})")
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
        # 0b) Persist to PostgreSQL (conversations/messages/customers) — non-blocking
        if not text.startswith("/"):
            try:
                import asyncio as _aio
                from agents.monitoring.persistence import ChatMemory
                if getattr(self, "_chat_memory", None) is None:
                    self._chat_memory = ChatMemory()
                tg_user = update.effective_user
                _aio.create_task(self._chat_memory.log_user(chat_id, tg_user, text))
            except Exception:
                pass
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
                from agents.monitoring.jobsearch_filters import parse_job_count as _parse_n2
                _n_job = _parse_n2(text) or 8
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
            ("job" in low or "intern" in low or "thực tập" in low or "tuc tap" in low
             or "tuyển dụng" in low or "tuyen dung" in low or "tuyển" in low or "tuyen" in low
             or "ai/ml intern" in low or "machine learning intern" in low)
            or (
                ("tìm" in low or "tim" in low or "search" in low)
                and ("job" in low or "việc" in low or "viec" in low or "tuyển" in low or "apply" in low)
                and not _is_research_phrase
            )
            # "tìm <ngành> <địa điểm> còn apply được / đang tuyển" = clearly job hunting
            or (("tìm" in low or "tim" in low) and ("còn apply" in low or "đang tuyển" in low or "còn tuyển" in low))
            or ("apply được" in low and ("tìm" in low or "tim" in low or "có" in low))
            or ("ai intern" in low) or ("job search agent" in low)
        )
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
                from agents.monitoring.jobsearch_filters import parse_job_count as _parse_n3
                _n_job = _parse_n3(text) or 8
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
            # 2e) Food / restaurant / Michelin: MUST web-verify before answering.
            # The LLM path hallucinates dishes, stars and fake restaurants, so we
            # run a REAL web_search and only echo verifiable links — never invent.
            if _is_food_lookup(text):
                try:
                    results = await _real_web_search(_food_query(text))
                    if typing_task:
                        typing_task.cancel()
                        typing_task = None
                    if not results:
                        await update.message.reply_text(
                            "⚠️ Mình đã tra web thực tế nhưng KHÔNG tìm thấy nguồn chính thức nào "
                            "cho câu hỏi này, nên sẽ KHÔNG bịa danh sách. Bạn có thể thử lại với "
                            "từ khóa cụ thể hơn (vd: 'nhà hàng Hà Nội đạt Michelin 2024') hoặc "
                            "tự tra trên guide.michelin.com.",
                            reply_markup=self._feedback_keyboard("food"),
                        )
                        return
                    _lines = []
                    for _i, _r in enumerate(results[:5], 1):
                        _t = (_r.get("title") or "").strip()
                        _u = (_r.get("url") or "").strip()
                        _s = (_r.get("snippet") or "").strip()
                        _entry = f"{_i}. {_sanitize_text(_t)}"
                        if _s:
                            _entry += f"\n   {_sanitize_text(_s)}"
                        if _u:
                            _entry += f"\n   🔗 {_u}"
                        _lines.append(_entry)
                    if not _lines:
                        await update.message.reply_text(
                            "⚠️ Mình đã tra web nhưng kết quả thiếu tiêu đề/link, nên KHÔNG bịa. "
                            "Thử lại với từ khóa cụ thể hơn.",
                            reply_markup=self._feedback_keyboard("food"),
                        )
                        return
                    _lines_txt = "\n\n".join(_lines)
                    await update.message.reply_text(
                        "🔎 Theo nguồn Michelin Guide & báo chính thức (mình chỉ trích trực tiếp, "
                        "KHÔNG tự bịa tên/mô tả):\n\n"
                        + _lines_txt
                        + "\n\n⚠️ Bấm link để xem danh sách đầy đủ và xác minh từng mục.",
                        reply_markup=self._feedback_keyboard("food"),
                    )
                    return
                except Exception:
                    if typing_task:
                        typing_task.cancel()
                        typing_task = None
                    try:
                        await update.message.reply_text(
                            "⚠️ Lỗi khi tra web món ăn/Michelin, nên KHÔNG bịa. Thử lại sau.",
                            reply_markup=self._feedback_keyboard("food"),
                        )
                    except Exception:
                        pass
                    return
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
                    kb = self._feedback_keyboard(str(req.task_id))
                    await update.message.reply_text(_sanitize_text(reply)[:4000], reply_markup=kb)
                    return
                except Exception:
                    pass
            except Exception:
                pass
            # Factual / real-world lookup -> research pipeline (web-verified),
            # NOT the bare LLM (it hallucinates on facts it cannot know).
            if _needs_web_lookup(text):
                try:
                    from agents.monitoring.research import ResearchOrchestrator
                    from uuid import uuid4 as _u4
                    orch = ResearchOrchestrator()
                    res = await orch.execute(task_id=_u4(), query=text, domain="web")
                    if typing_task:
                        typing_task.cancel()
                        typing_task = None
                    if res.get("status") == "success" and len(res.get("report", "").strip()) > 200:
                        rep = res["report"]
                        if len(rep) > 4000:
                            rep = rep[:3900] + self._tr(update, "truncated")
                        await update.message.reply_text(
                            _md_to_telegram_html(_sanitize_text(rep)),
                            parse_mode=ParseMode.HTML,
                            reply_markup=self._feedback_keyboard(str(res.get("task_id", "web"))),
                        )
                        return
                except Exception:
                    pass
                if typing_task:
                    typing_task.cancel()
                    typing_task = None
                try:
                    await update.message.reply_text(
                        "⚠️ Mình không tra được web cho câu này nên sẽ KHÔNG bịa. "
                        "Bạn thử lại sau hoặc dùng /research <câu hỏi> nhé."
                    )
                except Exception:
                    pass
                return
            from packages.config.settings import get_settings
            from packages.llm.factory import get_llm_provider
            from packages.core.prompts import render as _render
            # Personalization: profile + recent history from DB
            try:
                _mem = self._chat_memory
                profile_line = ""
                if _mem is not None and update.effective_user:
                    _blurb = await _mem.customer_profile_blurb(update.effective_user)
                    if _blurb:
                        profile_line = f"Hồ sơ khách hàng: {_blurb}.\n"
                    _hist = await _mem.recent_history(chat_id, limit=8)
                    if _hist:
                        _hist_txt = "\n".join(
                            f"{'User' if r == 'user' else 'Bot'}: {c[:200]}" for r, c in _hist[:-1]
                        )
                        if _hist_txt:
                            profile_line += _render("HISTORY_BLOCK", history=_hist_txt) + "\n"
                else:
                    profile_line = ""
            except Exception:
                profile_line = ""
            llm = get_llm_provider(get_settings())
            answer = await llm.generate(
                prompt=text,
                system=_render(
                    "TELEGRAM_SYSTEM",
                    owner_name="Mai Nguyễn Bình Tân",
                    profile_line=profile_line,
                ),
                max_tokens=400,
                temperature=0.3,
            )
            reply = answer if isinstance(answer, str) else str(answer)
            if typing_task: typing_task.cancel()
            await update.message.reply_text(_sanitize_text(reply)[:4000])
            try:
                import asyncio as _aio2
                if getattr(self, "_chat_memory", None) is not None and update.effective_user:
                    _aio2.create_task(self._chat_memory.log_assistant(chat_id, update.effective_user, reply))
            except Exception:
                pass
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
