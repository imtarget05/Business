# -*- coding: utf-8 -*-
"""Unit tests for progress_report module.

Mocks DB session + agent stats to avoid requiring live infrastructure.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from agents.monitoring.progress_report import (
    DailyReport,
    generate_daily_report,
    get_agent_stats,
)


class _FakeResult:
    """Minimal stand-in for sqlalchemy result.scalars()."""
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return self
    def all(self):
        return self._rows
    def execute(self, *a, **k):
        return self


class _FakeSession:
    """Async context manager + execute returning empty task list."""
    def __init__(self, rows=None):
        self._rows = rows or []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def execute(self, stmt):
        return _FakeResult(self._rows)


def _fake_session_factory(rows=None):
    def _factory():
        return _FakeSession(rows)
    return _factory


@pytest.mark.asyncio
async def test_daily_report_to_markdown_empty():
    """Empty report still renders markdown without error."""
    report = DailyReport(date="2024-01-01", generated_at=datetime.now(timezone.utc).isoformat())
    md = report.to_markdown()
    assert "Progress Report" in md
    assert "2024-01-01" in md


@pytest.mark.asyncio
async def test_daily_report_to_dict():
    """to_dict exposes aggregated fields."""
    report = DailyReport(
        date="2024-01-01",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_tasks_created=5,
        total_tasks_completed=4,
        success_rate=0.8,
        pending_tasks=1,
        failed_tasks=0,
    )
    d = report.to_dict()
    assert d["total_tasks_created"] == 5
    assert d["success_rate"] == 0.8
    assert d["recent_tasks_count"] == 0


@pytest.mark.asyncio
async def test_generate_daily_report_empty_db(monkeypatch):
    """generate_daily_report works with a fake (empty) session factory."""
    from agents.monitoring import progress_report as pr

    async def fake_agent_stats():
        return {"agent_count": 3, "llm_provider": "mock"}

    monkeypatch.setattr(pr, "get_agent_stats", fake_agent_stats)

    report = await generate_daily_report(hours=24, session_factory=_fake_session_factory())
    assert report.total_tasks_created == 0
    assert report.success_rate == 0.0
    md = report.to_markdown()
    assert "Progress Report" in md


@pytest.mark.asyncio
async def test_get_agent_stats_runs():
    """get_agent_stats returns a dict (graceful if container absent)."""
    stats = await get_agent_stats()
    assert isinstance(stats, dict)
