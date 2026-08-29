"""Unit tests for the metrics registry."""

from __future__ import annotations

from packages.observability.metrics import get_metrics


async def test_counter_and_timer() -> None:
    m = get_metrics()
    m.incr("tasks_total", outcome="success")
    m.incr("tasks_total", outcome="success")
    async with m.time("task_duration", outcome="success"):
        pass
    snap = m.snapshot()
    assert snap["counters"]["tasks_total|outcome=success"] == 2
    assert snap["timings"]["task_duration|outcome=success"]["count"] == 1
