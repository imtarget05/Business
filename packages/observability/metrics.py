"""In-process metrics registry (Phase 1 observability).

Zero-dependency counters/timers for agent execution telemetry. For pilot scale
this is exported via /health or scraped snapshots; swap the sink for
Prometheus pushgateway later without changing call sites.
"""

from __future__ import annotations

import time
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

_current_metrics: ContextVar[MetricsRegistry | None] = ContextVar(
    "boas_metrics", default=None
)


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.timings: dict[str, list[float]] = defaultdict(list)

    # -- counters ----------------------------------------------------------
    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        self.counters[f"{name}|{self._labels(labels)}"] += value

    # -- timers ------------------------------------------------------------
    def observe(self, name: str, duration_s: float, **labels: str) -> None:
        self.timings[f"{name}|{self._labels(labels)}"].append(duration_s)

    def time(self, name: str, **labels: str):
        """Async context manager: async with metrics.time('task_duration'): ..."""
        return _Timer(self, name, labels)

    # -- export ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "timings": {
                key: {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values) * 1000, 2)
                    if values
                    else 0.0,
                    "max_ms": round(max(values) * 1000, 2) if values else 0.0,
                }
                for key, values in self.timings.items()
            },
        }

    @staticmethod
    def _labels(labels: dict[str, str]) -> str:
        return ",".join(f"{k}={v}" for k, v in sorted(labels.items())) if labels else "-"


class _Timer:
    def __init__(self, registry: MetricsRegistry, name: str, labels: dict[str, str]) -> None:
        self._registry = registry
        self._name = name
        self._labels = labels

    async def __aenter__(self) -> _Timer:
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, *exc) -> None:
        self._registry.observe(self._name, time.perf_counter() - self._start, **self._labels)


def get_metrics() -> MetricsRegistry:
    """Process-wide registry (per asyncio run loop / worker)."""
    reg = _current_metrics.get()
    if reg is None:
        reg = MetricsRegistry()
        _current_metrics.set(reg)
    return reg


__all__ = ["MetricsRegistry", "get_metrics"]
