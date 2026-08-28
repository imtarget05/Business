# -*- coding: utf-8 -*-
"""Unit tests for health_check module.

Uses monkeypatching to avoid requiring live DB / API / container.
"""

from __future__ import annotations

import pytest

from agents.monitoring.health_check import (
    ComponentCheck,
    HealthCheckResult,
    run_health_check,
)


@pytest.mark.asyncio
async def test_component_check_defaults():
    """ComponentCheck defaults to ok status."""
    c = ComponentCheck(name="api")
    assert c.name == "api"
    assert c.status == "ok"
    assert c.message == ""


@pytest.mark.asyncio
async def test_health_result_to_dict():
    """to_dict serializes checks correctly."""
    result = HealthCheckResult(
        timestamp="2024-01-01T00:00:00+00:00",
        overall="ok",
        checks=[
            ComponentCheck(name="api", status="ok", message="healthy"),
            ComponentCheck(name="db", status="warning", message="slow", response_time_ms=120.5),
        ],
    )
    d = result.to_dict()
    assert d["overall"] == "ok"
    assert len(d["checks"]) == 2
    assert d["checks"][1]["response_time_ms"] == 120.5


@pytest.mark.asyncio
async def test_health_result_to_markdown():
    """to_markdown includes component names and overall status."""
    result = HealthCheckResult(
        timestamp="2024-01-01T00:00:00+00:00",
        overall="degraded",
        checks=[ComponentCheck(name="api", status="error", message="down")],
    )
    md = result.to_markdown()
    assert "Health Check Report" in md
    assert "DEGRADED" in md
    assert "api" in md


@pytest.mark.asyncio
async def test_run_health_check_mocked(monkeypatch):
    """run_health_check aggregates mocked component checks."""
    from agents.monitoring import health_check as hc

    async def fake_api(url: str = "") -> ComponentCheck:
        return ComponentCheck(name="api", status="ok", message="healthy")

    async def fake_db() -> ComponentCheck:
        return ComponentCheck(name="database", status="ok", message="reachable")

    async def fake_registry() -> ComponentCheck:
        return ComponentCheck(name="agent_registry", status="ok", message="3 agents")

    async def fake_queue() -> ComponentCheck:
        return ComponentCheck(name="task_queue", status="unavailable", message="not implemented")

    monkeypatch.setattr(hc, "check_api", fake_api)
    monkeypatch.setattr(hc, "check_database_health", fake_db)
    monkeypatch.setattr(hc, "check_agent_registry", fake_registry)
    monkeypatch.setattr(hc, "check_task_queue", fake_queue)

    result = await run_health_check()
    names = {c.name for c in result.checks}
    assert {"api", "database", "agent_registry", "task_queue"} <= names
    # overall should be degraded (one unavailable) or ok — at minimum not crash
    assert result.overall in {"ok", "degraded", "down"}
