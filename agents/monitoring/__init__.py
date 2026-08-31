"""Monitoring agents package.

Packages:
- agents/monitoring/health_check.py — system health checks
- agents/monitoring/progress_report.py — daily progress reports
- agents/monitoring/telegram_bot.py — Telegram bot for commands + push
- agents/monitoring/scheduler.py — schedule routine
- agents/monitoring/research/ — research agents (web, arxiv)

Usage:
    # Run health check
    from agents.monitoring.health_check import run_health_check
    health = await run_health_check()
    print(health.to_dict())

    # Generate daily report
    from agents.monitoring.progress_report import generate_daily_report
    report = await generate_daily_report()
    print(report.to_markdown())

    # Run research
    from agents.monitoring.research import ResearchOrchestrator
    from uuid import uuid4
    orch = ResearchOrchestrator()
    result = await orch.execute(task_id=uuid4(), query="What is LangGraph?", domain="web")
    print(result.get("report", ""))

    # Start Telegram bot
    from agents.monitoring.telegram_bot import MonitoringBot, TelegramConfig
    config = TelegramConfig(bot_token="xxx", chat_id="yyy")
    bot = MonitoringBot(config)
    await bot.initialize()
    await bot.start()

    # Start scheduler
    from agents.monitoring.scheduler import MonitoringScheduler, SchedulerConfig
    config = SchedulerConfig(health_check_interval_minutes=30)
    scheduler = MonitoringScheduler(config)
    await scheduler.start()
"""

from __future__ import annotations

__all__ = [
    "health_check",
    "progress_report",
    "telegram_bot",
    "scheduler",
    "research",
]
