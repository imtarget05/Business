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
    from telegram import Update, Bot
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
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
    
    async def initialize(self) -> None:
        """Initialize bot application."""
        self.app = Application.builder().token(self.config.bot_token).build()
        
        # Command handlers
        self.app.add_handler(CommandHandler("health", self._health_command))
        self.app.add_handler(CommandHandler("report", self._report_command))
        self.app.add_handler(CommandHandler("research", self._research_command))
        self.app.add_handler(CommandHandler("help", self._help_command))
        
        # Message handler for research queries (when awaiting)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message_handler))
        
        await self.app.initialize()
    
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
            "*📋 Monitoring Bot Commands*\n\n"
            "*`/health`* — Get current system health status\n"
            "*`/report`* — Get daily progress report\n"
            "*`/research <query>`* — Run web research on query\n"
            "*`/help`* — Show this help\n\n"
            "*Scheduled:*\n"
            "- Health check every 30 minutes\n"
            "- Daily report at 09:00 AM\n\n"
            "*Alerts:*\n"
            "- System health alerts pushed when degraded/down"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def _message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages (for research queries when awaiting)."""
        chat_id = update.effective_chat.id
        if chat_id in self._research_awaiting:
            query = self._research_awaiting.pop(chat_id)
            # Forward message as research query
            await self._research_command(update, context)


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
    
    # Test: send a message
    await bot.send_message("🤖 Monitoring bot is ready!")
    
    print("Bot started. Press Ctrl+C to stop.")
    await bot.start()
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
