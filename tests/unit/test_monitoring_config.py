"""Unit tests for monitoring config loader."""

from __future__ import annotations

import pytest

from agents.monitoring.config import (
    MonitoringConfig,
    load_monitoring_config,
)


@pytest.mark.asyncio
async def test_load_default_config(tmp_path, monkeypatch):
    """Without config.yaml, returns defaults (telegram off)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Point loader at a nonexistent path
    cfg = load_monitoring_config(path=str(tmp_path / "nope.yaml"))
    assert isinstance(cfg, MonitoringConfig)
    assert cfg.telegram.enabled is False
    assert cfg.scheduler.health_check_interval_minutes == 30


@pytest.mark.asyncio
async def test_load_from_yaml(tmp_path, monkeypatch):
    """Loads monitoring section from a YAML file."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    yaml_text = """
monitoring:
  enabled: true
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  scheduler:
    health_check_interval_minutes: 15
    daily_report_cron: "30 8 * * *"
  health:
    api_base_url: "http://example:9000"
  research:
    max_results: 10
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text, encoding="utf-8")

    cfg = load_monitoring_config(path=str(p))
    assert cfg.scheduler.health_check_interval_minutes == 15
    assert cfg.scheduler.daily_report_time.hour == 8
    assert cfg.scheduler.daily_report_time.minute == 30
    assert cfg.health.api_base_url == "http://example:9000"
    assert cfg.research.max_results == 10
    # Placeholder tokens ignored (real value from env)
    assert cfg.telegram.bot_token is None


@pytest.mark.asyncio
async def test_env_override(monkeypatch):
    """Environment variables override file/default values."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token_123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat_456")
    monkeypatch.setenv("HEALTH_CHECK_INTERVAL_MINUTES", "45")

    cfg = load_monitoring_config(path="/nonexistent.yaml")
    assert cfg.telegram.bot_token == "env_token_123"
    assert cfg.telegram.chat_id == "env_chat_456"
    assert cfg.telegram.enabled is True
    assert cfg.scheduler.health_check_interval_minutes == 45
