"""Configuration loader for the monitoring agent.

Reads config.yaml (if present) and merges with environment variables.
Environment variables take precedence over file values.

Env vars:
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> telegram push credentials
- MONITORING_SCHEDULER_ENABLED          -> "true"/"false"
- HEALTH_CHECK_INTERVAL_MINUTES         -> int
- DAILY_REPORT_CRON                    -> cron expression (e.g. "0 9 * * *")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import time
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None


@dataclass
class SchedulerConfig:
    enabled: bool = True
    health_check_interval_minutes: int = 30
    daily_report_time: time = field(default_factory=lambda: time(9, 0))
    time_zone: str = "Asia/Ho_Chi_Minh"  # Vietnam (UTC+7) — Business Ops Hub runs here


@dataclass
class HealthConfig:
    api_base_url: str = "http://localhost:8000"
    db_check: bool = True
    agent_registry_check: bool = True


@dataclass
class ResearchConfig:
    web_enabled: bool = True
    arxiv_enabled: bool = True
    max_results: int = 5
    extract_char_limit: int = 5000


@dataclass
class MonitoringConfig:
    enabled: bool = True
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)


def _parse_daily_report_time(cron: str, default: time) -> time:
    """Parse 'M H * * *' cron into a time object (minute, hour)."""
    parts = cron.split()
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return time(int(parts[1]), int(parts[0]))
    return default


def load_monitoring_config(path: str | None = None) -> MonitoringConfig:
    """Load monitoring config from YAML + env overrides.

    Args:
        path: Path to config.yaml. Defaults to DEFAULT_CONFIG_PATH if it exists.
    """
    cfg = MonitoringConfig()

    # 1. File-based config
    resolved = path or (DEFAULT_CONFIG_PATH if os.path.exists(DEFAULT_CONFIG_PATH) else None)
    if resolved and os.path.exists(resolved):
        try:
            with open(resolved, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            mon = data.get("monitoring", {})
            cfg.enabled = mon.get("enabled", cfg.enabled)

            tg = mon.get("telegram", {})
            cfg.telegram.enabled = tg.get("enabled", cfg.telegram.enabled)
            # tokens come from env (never stored in plaintext in file besides placeholder)
            token = tg.get("bot_token")
            chat = tg.get("chat_id")
            cfg.telegram.bot_token = _strip_env_placeholder(token) or cfg.telegram.bot_token
            cfg.telegram.chat_id = _strip_env_placeholder(chat) or cfg.telegram.chat_id

            sch = mon.get("scheduler", {})
            cfg.scheduler.enabled = sch.get("enabled", cfg.scheduler.enabled)
            cfg.scheduler.health_check_interval_minutes = sch.get(
                "health_check_interval_minutes", cfg.scheduler.health_check_interval_minutes
            )
            cron = sch.get("daily_report_cron")
            if cron:
                cfg.scheduler.daily_report_time = _parse_daily_report_time(
                    cron, cfg.scheduler.daily_report_time
                )

            hc = mon.get("health", {})
            cfg.health.api_base_url = hc.get("api_base_url", cfg.health.api_base_url)
            cfg.health.db_check = hc.get("db_check", cfg.health.db_check)
            cfg.health.agent_registry_check = hc.get(
                "agent_registry_check", cfg.health.agent_registry_check
            )

            rc = mon.get("research", {})
            cfg.research.web_enabled = rc.get("web_enabled", cfg.research.web_enabled)
            cfg.research.arxiv_enabled = rc.get("arxiv_enabled", cfg.research.arxiv_enabled)
            cfg.research.max_results = rc.get("max_results", cfg.research.max_results)
            cfg.research.extract_char_limit = rc.get(
                "extract_char_limit", cfg.research.extract_char_limit
            )
        except Exception as e:
            logger.warning(f"Failed to load monitoring config from {resolved}: {e}")

    # 2. Environment overrides (highest precedence)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.telegram.bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
        cfg.telegram.enabled = True
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg.telegram.chat_id = os.environ["TELEGRAM_CHAT_ID"]

    if os.environ.get("MONITORING_SCHEDULER_ENABLED"):
        cfg.scheduler.enabled = os.environ["MONITORING_SCHEDULER_ENABLED"].lower() == "true"
    if os.environ.get("HEALTH_CHECK_INTERVAL_MINUTES"):
        try:
            cfg.scheduler.health_check_interval_minutes = int(
                os.environ["HEALTH_CHECK_INTERVAL_MINUTES"]
            )
        except ValueError:
            pass
    if os.environ.get("DAILY_REPORT_CRON"):
        cfg.scheduler.daily_report_time = _parse_daily_report_time(
            os.environ["DAILY_REPORT_CRON"], cfg.scheduler.daily_report_time
        )

    return cfg


def _strip_env_placeholder(value: Any) -> str | None:
    """config.yaml uses ${VAR} placeholders; ignore them (real value comes from env)."""
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        return None
    return value
