"""Scheduler for monitoring agent — runs health checks and daily reports.

Schedule:
- Health check: every 30 minutes
- Daily report: 09:00 AM daily (push to Telegram if configured)
- Research agents: triggered by Telegram commands (not scheduled)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.monitoring.health_check import run_health_check
from agents.monitoring.progress_report import generate_daily_report
from agents.ops_hub import build_task_provider, create_ops_hub_agent
from packages.contracts.enums import AgentResponseStatus, Domain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    """Scheduler configuration."""
    health_check_interval_minutes: int = 30
    daily_report_time: time = time(9, 0)  # 09:00 AM
    time_zone: str = "Asia/Seoul"  # SE Asia Standard Time


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class MonitoringScheduler:
    """APScheduler-based scheduler for monitoring tasks."""
    
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.scheduler: AsyncIOScheduler | None = None
        self._telegram_config: Any = None  # Set later if Telegram available
        self._telegram_bot: Any = None
    
    def set_telegram(self, bot: Any, config: Any) -> None:
        """Set Telegram bot for push notifications."""
        self._telegram_bot = bot
        self._telegram_config = config
    
    async def initialize(self) -> None:
        """Initialize scheduler."""
        self.scheduler = AsyncIOScheduler(timezone=self.config.time_zone)
        
        # Health check every N minutes
        self.scheduler.add_job(
            self._run_health_check_job,
            IntervalTrigger(minutes=self.config.health_check_interval_minutes),
            id="health_check",
            name="Periodic health check",
            replace_existing=True,
        )
        
        # Daily report at specified time
        self.scheduler.add_job(
            self._run_daily_report_job,
            CronTrigger(
                hour=self.config.daily_report_time.hour,
                minute=self.config.daily_report_time.minute,
            ),
            id="daily_report",
            name="Daily progress report",
            replace_existing=True,
        )
        
        # Learning loop cycle (ADR-010) — daily at configured UTC hour
        try:
            from packages.config.settings import get_settings

            learning_hour = get_settings().learning_cron_hour
        except Exception:  # noqa: BLE001
            learning_hour = 3
        self.scheduler.add_job(
            self._run_learning_job,
            CronTrigger(hour=learning_hour, minute=0),
            id="learning_cycle",
            name="Daily learning cycle",
            replace_existing=True,
        )

        # Business Ops Hub daily digest (Task 2) — 08:00 local time.
        self.scheduler.add_job(
            self._run_ops_hub_job,
            CronTrigger(hour=8, minute=0),
            id="ops_hub_daily",
            name="Business Ops Hub daily digest",
            replace_existing=True,
        )

        logger.info(
            f"Scheduler initialized: health every {self.config.health_check_interval_minutes}m, "
            f"daily report at {self.config.daily_report_time.strftime('%H:%M')}, "
            f"learning at {learning_hour:02d}:00, "
            f"ops hub digest at 08:00"
        )
    
    async def start(self) -> None:
        """Start scheduler."""
        if not self.scheduler:
            await self.initialize()
        self.scheduler.start()
        logger.info("Scheduler started")
    
    async def stop(self) -> None:
        """Stop scheduler."""
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
    
    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    
    async def _run_health_check_job(self) -> None:
        """Execute health check job."""
        from packages.core.tracing import get_tracer

        tracer = get_tracer()
        try:
            logger.info("Running scheduled health check...")
            with tracer.span("scheduled_health_check"):
                health = await run_health_check()
                health_dict = health.to_dict()

                # Push alert if degraded/down
                if self._telegram_bot and health_dict["overall"] != "ok":
                    await self._telegram_bot.send_health_alert(health_dict)

                # Log result
                    logger.info(
        "Health check: overall=%s, checks=%d",
        health_dict["overall"],
        len(health_dict["checks"]),
    )
                tracer.event("health_check_scheduled", overall=health_dict["overall"])
        except Exception as e:
            logger.error(f"Health check job error: {e}")

    async def _run_learning_job(self) -> None:
        """Run the daily learning cycle (ADR-010)."""
        try:
            from packages.core.bootstrap import get_container

            container = get_container()
            learning = getattr(container, "learning", None)
            if learning is None:
                return
            report = await learning.run_cycle()
            logger.info(
                f"Learning cycle done: {report['feedback_count']} feedback, "
                f"{report['rules_total']} rules"
            )
            if self._telegram_bot and hasattr(self._telegram_bot, "send_message"):
                summary = (
                    f"Learning cycle: {report['feedback_count']} feedback, "
                    f"{report['rules_total']} routing rules"
                )
                try:
                    await self._telegram_bot.send_message(summary)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:
            logger.error(f"Learning job error: {e}")

    async def _run_daily_report_job(self) -> None:
        """Execute daily report job."""
        from packages.core.tracing import get_tracer

        tracer = get_tracer()
        try:
            logger.info("Running scheduled daily report...")
            with tracer.span("scheduled_daily_report"):
                report = await generate_daily_report()
                md = report.to_markdown()

                # Emit trace event summarizing the report (Phase E3)
                tracer.event(
                    "daily_report_generated",
                    total_agents=report.summary.get("total_agents", 0),
                    total_executions=report.summary.get("total_executions", 0),
                )

                # Push to Telegram if configured
                if self._telegram_bot:
                    await self._telegram_bot.send_daily_report(md)
                    logger.info("Daily report sent to Telegram")
                else:
                    logger.info(f"Daily report generated: {report.summary_text}")
        except Exception as e:
            logger.error(f"Daily report job error: {e}")

    async def _run_ops_hub_job(self) -> None:
        """Execute the daily Business Ops Hub digest job (Task 2).

        Builds the digest via the registry's ``ops.digest`` capability and
        pushes it to Telegram (if configured). Degrades gracefully on error.
        """
        try:
            from packages.core.bootstrap import get_container

            ctn = get_container()
            desc, handler = ctn.registry.get_by_capability("ops.digest")
            import uuid as _uuid
            from packages.contracts.models import TaskContext, TaskRequest

            resp = await handler.handle(
                TaskRequest(
                    task_id=_uuid.uuid4(),
                    domain=Domain.OPS,
                    action="digest",
                    payload={},
                    context=TaskContext(
                        organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                        channel="scheduler",
                    ),
                )
            )
            if resp.status != AgentResponseStatus.SUCCESS or not resp.result:
                logger.warning("ops.digest returned %s — skip push", resp.status)
                return
            digest_dict = resp.result
            text = _format_ops_digest(digest_dict)
            if self._telegram_bot and hasattr(self._telegram_bot, "send_message"):
                try:
                    await self._telegram_bot.send_message(text)
                    logger.info("Ops Hub daily digest sent to Telegram")
                except Exception:  # noqa: BLE001
                    pass
            else:
                logger.info("Ops Hub digest (no Telegram): %s", text[:200])
        except Exception as e:
            logger.error(f"Ops Hub job error: {e}")


def _format_ops_digest(digest: dict[str, Any]) -> str:
    """Render an ``ops.digest`` result dict into a compact Telegram message."""
    summary = digest.get("summary", "")
    counts = digest.get("counts", {})
    alerts = digest.get("alerts", [])
    items = digest.get("items", [])
    lines = ["*📥 Business Ops Hub — Daily Digest*", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    lines.append(
        f"📧 Chưa đọc: {counts.get('emails_unread', 0)} | "
        f"📅 Sự kiện: {counts.get('events_upcoming', 0)} | "
        f"✅ Công việc: {counts.get('tasks_open', 0)}"
    )
    if alerts:
        lines.append("")
        lines.append("*🚨 Cần làm ngay:*")
        for a in alerts[:10]:
            lines.append(f"• {a.get('detail') or a.get('title')}")
    if items:
        lines.append("")
        lines.append("*📋 Chi tiết:*")
        for it in items[:15]:
            icon = {"email": "📧", "event": "📅", "task": "✅"}.get(it.get("kind"), "•")
            lines.append(f"{icon} {it.get('title')} — {it.get('detail')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def main() -> None:
    """CLI entry point — run scheduler for demo."""
    from agents.monitoring.config import load_monitoring_config

    cfg = load_monitoring_config()

    # Map MonitoringConfig -> SchedulerConfig (dataclass used by MonitoringScheduler)
    scheduler_cfg = SchedulerConfig(
        enabled=cfg.scheduler.enabled,
        health_check_interval_minutes=cfg.scheduler.health_check_interval_minutes,
        daily_report_time=cfg.scheduler.daily_report_time,
        time_zone=cfg.scheduler.time_zone,
    )

    # Telegram config (token/chat from config loader, which prefers env)
    bot = None
    if cfg.telegram.enabled and cfg.telegram.bot_token:
        from agents.monitoring.telegram_bot import MonitoringBot
        from agents.monitoring.telegram_bot import TelegramConfig as TC
        telegram_config = TC(bot_token=cfg.telegram.bot_token, chat_id=cfg.telegram.chat_id)
        bot = MonitoringBot(telegram_config)
        await bot.initialize()
        logger.info("Telegram configured for push notifications")
    elif cfg.telegram.bot_token:
        # token present but section disabled — still wire for alerts
        from agents.monitoring.telegram_bot import MonitoringBot
        from agents.monitoring.telegram_bot import TelegramConfig as TC
        telegram_config = TC(bot_token=cfg.telegram.bot_token, chat_id=cfg.telegram.chat_id)
        bot = MonitoringBot(telegram_config)
        await bot.initialize()

    scheduler = MonitoringScheduler(scheduler_cfg)
    if bot:
        scheduler.set_telegram(bot, scheduler_cfg)

    if not cfg.scheduler.enabled:
        logger.info("Scheduler disabled via config; exiting.")
        return

    await scheduler.start()

    print(f"Scheduler running: health every {scheduler_cfg.health_check_interval_minutes}m, "
          f"daily report at {scheduler_cfg.daily_report_time.strftime('%H:%M')}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await scheduler.stop()
        if bot:
            await bot.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
