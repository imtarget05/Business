# -*- coding: utf-8 -*-
"""Task 2 — Business Ops Hub unit tests.

Covers the OpsHubAgent digest aggregation with fully mocked Gmail / Calendar /
task sources (no network, no credentials, fast). Asserts digest structure,
count correctness, and alert derivation rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.ops_hub.agent import OpsHubAgent
from agents.ops_hub.tasks_provider import (
    InMemoryTaskProvider,
    StaticTaskProvider,
    Task,
    build_task_provider,
)
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, TaskRequest
from packages.llm.mock import MockLLMProvider


def _iso(offset_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


def _mock_gmail(unread: list[dict]) -> object:
    async def _src() -> list[dict]:
        return unread

    return _src


def _mock_calendar(events: list[dict]) -> object:
    async def _src() -> list[dict]:
        return events

    return _src


# ---------------------------------------------------------------------------
# Agent construction / capability
# ---------------------------------------------------------------------------
def test_agent_registers_ops_digest_capability() -> None:
    agent = OpsHubAgent(task_provider=StaticTaskProvider([]))
    assert agent.descriptor.domain is Domain.OPS
    assert "ops.digest" in agent.descriptor.capabilities
    assert agent.descriptor.qualified_name == "ops_hub-v1"


# ---------------------------------------------------------------------------
# Digest structure
# ---------------------------------------------------------------------------
async def test_build_digest_empty_sources() -> None:
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=_mock_calendar([]),
        task_provider=StaticTaskProvider([]),
        llm=MockLLMProvider(),
    )
    digest = await agent.build_digest()
    assert digest.counts == {
        "emails_unread": 0,
        "events_upcoming": 0,
        "tasks_open": 0,
        "alerts": 0,
    }
    assert digest.summary  # fallback summary produced (LLM scripted empty -> fallback)
    assert digest.items == []
    assert digest.alerts == []


async def test_build_digest_aggregates_all_sources() -> None:
    gmail = _mock_gmail(
        [
            {"id": "m1", "subject": "Invoice overdue", "from": "vendor@x.com", "read": False},
            {"id": "m2", "subject": "Newsletter", "from": "noreply@y.com", "read": True},
        ]
    )
    calendar = _mock_calendar(
        [{"id": "e1", "summary": "Standup", "start": {"dateTime": _iso(2)}}]
    )
    tasks = StaticTaskProvider(
        [Task(id="t1", title="Renew domain", priority="high")]
    )
    agent = OpsHubAgent(
        gmail_source=gmail,
        calendar_source=calendar,
        task_provider=tasks,
        llm=MockLLMProvider(),
    )
    digest = await agent.build_digest()

    # Only unread emails counted
    assert digest.counts["emails_unread"] == 1
    assert digest.counts["events_upcoming"] == 1
    assert digest.counts["tasks_open"] == 1

    kinds = {i.kind for i in digest.items}
    assert kinds == {"email", "event", "task"}

    # Upcoming event (+2h window) -> event alert; high-priority task -> task alert.
    assert digest.counts["alerts"] == 2
    assert any(a.kind == "task" and a.priority == "high" for a in digest.alerts)
    assert any(a.kind == "event" for a in digest.alerts)


async def test_flagged_email_creates_high_alert() -> None:
    gmail = _mock_gmail(
        [{"id": "m1", "subject": "URGENT", "from": "boss@x.com", "read": False, "flagged": True}]
    )
    agent = OpsHubAgent(
        gmail_source=gmail,
        calendar_source=_mock_calendar([]),
        task_provider=StaticTaskProvider([]),
    )
    digest = await agent.build_digest()
    assert digest.counts["emails_unread"] == 1
    assert any(a.kind == "email" and a.priority == "high" for a in digest.alerts)


# ---------------------------------------------------------------------------
# Alert window (upcoming event / due task within 24h)
# ---------------------------------------------------------------------------
async def test_upcoming_event_within_window_is_alert() -> None:
    calendar = _mock_calendar(
        [{"id": "e1", "summary": "Call client", "start": {"dateTime": _iso(3)}}]
    )
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=calendar,
        task_provider=StaticTaskProvider([]),
    )
    digest = await agent.build_digest()
    assert any(a.kind == "event" for a in digest.alerts)


async def test_far_event_not_alerted() -> None:
    calendar = _mock_calendar(
        [{"id": "e1", "summary": "Annual review", "start": {"dateTime": _iso(72)}}]
    )
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=calendar,
        task_provider=StaticTaskProvider([]),
    )
    digest = await agent.build_digest()
    assert digest.counts["events_upcoming"] == 1
    assert not any(a.kind == "event" for a in digest.alerts)


async def test_due_task_within_window_is_alert() -> None:
    due = datetime.now(timezone.utc) + timedelta(hours=5)
    tasks = StaticTaskProvider([Task(id="t1", title="Pay tax", due=due)])
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=_mock_calendar([]),
        task_provider=tasks,
    )
    digest = await agent.build_digest()
    assert any(a.kind == "task" for a in digest.alerts)


# ---------------------------------------------------------------------------
# handle() envelope
# ---------------------------------------------------------------------------
async def test_handle_returns_success_envelope() -> None:
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=_mock_calendar([]),
        task_provider=StaticTaskProvider([]),
        llm=MockLLMProvider(),
    )
    import uuid as _uuid

    resp = await agent.handle(
        TaskRequest(task_id=_uuid.uuid4(), domain=Domain.OPS, action="digest", payload={})
    )
    assert isinstance(resp, AgentResponse)
    assert resp.status is AgentResponseStatus.SUCCESS
    assert "items" in resp.result and "alerts" in resp.result and "counts" in resp.result


async def test_handle_rejects_unknown_action() -> None:
    agent = OpsHubAgent(task_provider=StaticTaskProvider([]))
    import uuid as _uuid

    resp = await agent.handle(
        TaskRequest(task_id=_uuid.uuid4(), domain=Domain.OPS, action="spawn", payload={})
    )
    assert resp.status is AgentResponseStatus.REJECTED
    assert resp.error is not None


# ---------------------------------------------------------------------------
# TaskProvider: InMemory (config.yaml shape) + build_task_provider
# ---------------------------------------------------------------------------
async def test_inmemory_task_provider_from_config_shape() -> None:
    provider = InMemoryTaskProvider(
        tasks=[
            {"title": "A", "priority": "high", "due": _iso(1)},
            "plain string task",
            {"title": "", "due": _iso(2)},  # skipped: no title
        ]
    )
    tasks = await provider.list_tasks()
    assert len(tasks) == 2
    assert {t.title for t in tasks} == {"A", "plain string task"}


def test_build_task_provider_default_empty() -> None:
    provider = build_task_provider()
    assert isinstance(provider, InMemoryTaskProvider)


# ---------------------------------------------------------------------------
# Regression: naive 'due' from shipped config.yaml must NOT crash build_digest()
# (CRITICAL — 'can't subtract offset-naive and offset-aware').
# ---------------------------------------------------------------------------
async def test_build_digest_with_shipped_config_tasks_naive_due() -> None:
    """Reproduce the SHIPPED config.yaml: task due is a naive ISO string.

    build_digest() must normalize it to aware UTC and return a Digest without
    raising TypeError (naive/aware subtraction).
    """
    # Mirror config.yaml ops.tasks exactly: one naive-ISO 'due', one no-due high.
    provider = InMemoryTaskProvider(
        tasks=[
            {"title": "Gửi báo cáo tuần cho khách hàng", "due": "2026-08-30T17:00:00", "priority": "normal"},
            {"title": "Duyệt báo giá nhà cung cấp A", "priority": "high"},
        ]
    )
    agent = OpsHubAgent(
        gmail_source=_mock_gmail([]),
        calendar_source=_mock_calendar([]),
        task_provider=provider,
        llm=MockLLMProvider(),
    )
    digest = await agent.build_digest()
    assert isinstance(digest, __import__("agents.ops_hub.agent", fromlist=["Digest"]).Digest)
    assert digest.counts["tasks_open"] == 2
    # High-priority task (no due) -> alert; naive-due task normalized to aware UTC.
    high = [a for a in digest.alerts if a.kind == "task" and a.priority == "high"]
    assert high, "high-priority task should still alert"
    due_task = next(i for i in digest.items if i.title.startswith("Gửi báo cáo"))
    assert due_task.due is not None and due_task.due.tzinfo is not None
    # Subtraction must work (no TypeError): aware UTC minus aware UTC.
    from agents.ops_hub.agent import _now

    assert (due_task.due - _now()).total_seconds() >= 0


# ---------------------------------------------------------------------------
# Scheduler timezone (IMPORTANT) — ops_hub_daily fires 08:00 Asia/Ho_Chi_Minh
# (UTC+7) == 01:00 UTC, not Asia/Seoul.
# ---------------------------------------------------------------------------
def test_scheduler_ops_hub_job_uses_vn_timezone() -> None:
    from agents.monitoring.scheduler import SchedulerConfig

    cfg = SchedulerConfig()
    assert cfg.time_zone == "Asia/Ho_Chi_Minh"

    # Build the trigger exactly as the scheduler does and confirm its next fire
    # time lands at 01:00 UTC (08:00 VN) rather than 00:00 UTC (08:00 Seoul).
    from zoneinfo import ZoneInfo
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(hour=8, minute=0, timezone=ZoneInfo("Asia/Ho_Chi_Minh"))
    # Pick a reference instant and compute the next run.
    ref = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)  # already past 08:00 VN that day
    nxt = trigger.get_next_fire_time(None, ref)
    assert nxt is not None
    # Fire time is expressed in the trigger's own tz (Asia/Ho_Chi_Minh, UTC+7);
    # verify the wall-clock is 08:00 local and the UTC instant is 01:00 (not 00:00 Seoul).
    assert nxt.utcoffset() == timedelta(seconds=25200)
    assert nxt.hour == 8 and nxt.minute == 0
    nxt_utc = nxt.astimezone(timezone.utc)
    # ~23h30m after ref (ref is 01:30 UTC, next fire at 01:00 UTC next day).
    assert 0 < (nxt_utc - ref).total_seconds() < 24 * 3600
    assert nxt_utc.hour == 1 and nxt_utc.minute == 0


# ---------------------------------------------------------------------------
# Scheduler formatter (shared with /ops route) — pure, no network
# ---------------------------------------------------------------------------
def test_format_ops_digest_renders_alerts_and_items() -> None:
    from agents.monitoring.scheduler import _format_ops_digest

    digest = {
        "summary": "Bạn có 1 email chưa đọc.",
        "counts": {"emails_unread": 1, "events_upcoming": 0, "tasks_open": 1, "alerts": 1},
        "alerts": [{"kind": "task", "title": "Pay tax", "detail": "📌 Công việc đến hạn: ..."}],
        "items": [
            {"kind": "email", "title": "URGENT", "detail": "Từ: boss@x.com"},
            {"kind": "task", "title": "Pay tax", "detail": "Ưu tiên: high"},
        ],
    }
    text = _format_ops_digest(digest)
    assert "📥 Business Ops Hub" in text
    assert "🚨 Cần làm ngay" in text
    assert "📋 Chi tiết" in text
    assert "URGENT" in text
    assert "Pay tax" in text
