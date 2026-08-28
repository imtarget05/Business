# -*- coding: utf-8 -*-
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
from datetime import datetime, time, timezone
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.monitoring.health_check import run_health_check
from agents.monitoring.progress_report import generate_daily_report

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
        
        logger.info(
            f"Scheduler initialized: health every {self.config.health_check_interval_minutes}m, "
            f"daily report at {self.config.daily_report_time.strftime('%H:%M')}"
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
        try:
            logger.info("Running scheduled health check...")
            health = await run_health_check()
            health_dict = health.to_dict()
            
            # Push alert if degraded/down
            if self._telegram_bot and health_dict["overall"] != "ok":
                await self._telegram_bot.send_health_alert(health_dict)
            
            # Log result
            logger.info(f"Health check: overall={health_dict['overall']}, checks={len(health_dict['checks'])}")
        except Exception as e:
            logger.error(f"Health check job error: {e}")
    
    async def _run_daily_report_job(self) -> None:
        """Execute daily report job."""
        try:
            logger.info("Running scheduled daily report...")
            report = await generate_daily_report()
            md = report.to_markdown()
            
            # Push to Telegram if configured
            if self._telegram_bot:
                await self._telegram_bot.send_daily_report(md)
                logger.info("Daily report sent to Telegram")
            else:
                logger.info(f"Daily report generated: {report.summary_text}")
        except Exception as e:
            logger.error(f"Daily report job error: {e}")


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
        from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig as TC
        telegram_config = TC(bot_token=cfg.telegram.bot_token, chat_id=cfg.telegram.chat_id)
        bot = MonitoringBot(telegram_config)
        await bot.initialize()
        logger.info("Telegram configured for push notifications")
    elif cfg.telegram.bot_token:
        # token present but section disabled — still wire for alerts
        from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig as TC
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
