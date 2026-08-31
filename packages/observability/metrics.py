"""In-process metrics registry + Prometheus bridge (Phase 1 observability).

Two sinks, one call site:

* :class:`MetricsRegistry` — zero-dependency counters/timers exported through
  ``snapshot()`` (used by the root-cause agent and ad-hoc debugging).
* Prometheus counters (``boas_*``) registered on the default
  ``prometheus_client`` registry and scraped from the API ``/metrics`` endpoint
  (Feature 3: Prometheus + Grafana dashboards).

The ``record_*`` helpers update BOTH sinks from a single call site so existing
snapshot-based behaviour never regresses. ``prometheus_client`` is treated as
optional: when it is missing the helpers degrade to registry-only updates and
log a structured warning instead of failing.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Any

# Cap retained timing samples per timer to bound memory in long-running
# processes (F14). Only the most recent samples are kept for snapshot stats.
_TIMING_BUFFER_MAXLEN = 1000

from packages.observability.logging import get_logger

logger = get_logger("observability.metrics")

_current_metrics: ContextVar[MetricsRegistry | None] = ContextVar("boas_metrics", default=None)

try:  # optional dependency: the API must stay importable without extras
    from prometheus_client import REGISTRY as PROMETHEUS_REGISTRY
    from prometheus_client import Counter as _PromCounter

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit on minimal installs
    PROMETHEUS_REGISTRY = None  # type: ignore[assignment]
    _PromCounter = None  # type: ignore[assignment]
    PROMETHEUS_AVAILABLE = False


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.timings: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_TIMING_BUFFER_MAXLEN)
        )

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
                    "avg_ms": round(sum(values) / len(values) * 1000, 2) if values else 0.0,
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


# ---------------------------------------------------------------------------
# Prometheus business metrics
# ---------------------------------------------------------------------------

_UNKNOWN = "unknown"


def _label(value: object | None, default: str = _UNKNOWN) -> str:
    """Prometheus label values must be non-empty strings."""
    text = str(value).strip() if value is not None else ""
    return text or default


def _find_registered(name: str) -> Any | None:
    """Return an already-registered collector for ``name`` (or None).

    ``prometheus_client`` strips the ``_total`` suffix from counter names, so we
    probe both spellings.
    """
    collectors = getattr(PROMETHEUS_REGISTRY, "_names_to_collectors", {})
    base = name[: -len("_total")] if name.endswith("_total") else name
    for candidate in (name, base):
        collector = collectors.get(candidate)
        if collector is not None:
            return collector
    return None


def _counter(name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Any | None:
    """Create — or re-use — a Counter on the default prometheus registry.

    Re-use matters because app factories and test suites import this module
    within one process repeatedly; a duplicate registration would otherwise
    raise ``ValueError: Duplicated timeseries in CollectorRegistry``.
    """
    if not PROMETHEUS_AVAILABLE:
        return None
    try:
        return _PromCounter(
            name, documentation, labelnames=labelnames, registry=PROMETHEUS_REGISTRY
        )
    except ValueError:
        existing = _find_registered(name)
        if existing is None:
            logger.warning("prometheus_counter_unavailable", extra={"metric": name})
        return existing


AGENT_SUCCESS_TOTAL = _counter(
    "boas_agent_success_total",
    "Agent executions by outcome (status=success|failed|escalated).",
    ("agent", "domain", "status"),
)
LLM_COST_USD_TOTAL = _counter(
    "boas_llm_cost_usd_total",
    "Estimated LLM spend in USD (from the llm_cost ledger estimates).",
    ("model", "tag"),
)
RAG_CACHE_HITS_TOTAL = _counter(
    "boas_rag_cache_hits_total",
    "RAG/prompt cache hits served without an LLM or web call.",
)
RAG_CACHE_MISSES_TOTAL = _counter(
    "boas_rag_cache_misses_total",
    "RAG/prompt cache misses that required a fresh answer.",
)
HANDOFF_TOTAL = _counter(
    "boas_handoff_total",
    "Agent-to-agent handoffs performed by the orchestrator.",
    ("from_agent", "to_agent"),
)


def _inc(counter: Any | None, value: float = 1.0, **labels: str) -> None:
    """Increment a prometheus counter defensively (telemetry never raises)."""
    if counter is None:
        return
    try:
        target = counter.labels(**labels) if labels else counter
        target.inc(value)
    except Exception:  # telemetry must never break the business flow
        logger.debug("prometheus_incr_failed", extra={"labels": labels})


def prometheus_enabled() -> bool:
    """True when prometheus_client is installed and the counters are live."""
    return PROMETHEUS_AVAILABLE and AGENT_SUCCESS_TOTAL is not None


def record_agent_result(
    agent: str,
    domain: str,
    status: str,
    *,
    duration_s: float | None = None,
) -> None:
    """Record one agent execution outcome in both metric sinks."""
    agent_l = _label(agent)
    domain_l = _label(domain)
    status_l = _label(status)
    registry = get_metrics()
    registry.incr("agent_runs_total", agent=agent_l, domain=domain_l, status=status_l)
    if duration_s is not None:
        registry.observe(
            "agent_duration_seconds",
            float(duration_s),
            agent=agent_l,
            domain=domain_l,
        )
    _inc(AGENT_SUCCESS_TOTAL, 1.0, agent=agent_l, domain=domain_l, status=status_l)


def record_llm_cost(model: str, cost_usd: float, *, tag: str = "") -> None:
    """Record estimated LLM spend (USD) for a model/tag pair."""
    model_l = _label(model, "unknown-model")
    tag_l = _label(tag, "untagged")
    amount = max(0.0, float(cost_usd or 0.0))
    get_metrics().incr("llm_cost_usd_total", amount, model=model_l, tag=tag_l)
    _inc(LLM_COST_USD_TOTAL, amount, model=model_l, tag=tag_l)


def record_rag_cache(hit: bool) -> None:
    """Record a RAG/prompt cache lookup outcome."""
    registry = get_metrics()
    if hit:
        registry.incr("rag_cache_total", outcome="hit")
        _inc(RAG_CACHE_HITS_TOTAL)
    else:
        registry.incr("rag_cache_total", outcome="miss")
        _inc(RAG_CACHE_MISSES_TOTAL)


def record_handoff(from_agent: str, to_agent: str) -> None:
    """Record an orchestrator handoff between two agents."""
    from_l = _label(from_agent)
    to_l = _label(to_agent)
    get_metrics().incr("handoff_total", from_agent=from_l, to_agent=to_l)
    _inc(HANDOFF_TOTAL, 1.0, from_agent=from_l, to_agent=to_l)


def render_prometheus_text() -> str:
    """Exposition-format snapshot of the default registry (debug/test helper)."""
    if not PROMETHEUS_AVAILABLE:
        return ""
    from prometheus_client import generate_latest

    return generate_latest(PROMETHEUS_REGISTRY).decode("utf-8")


__all__ = [
    "AGENT_SUCCESS_TOTAL",
    "HANDOFF_TOTAL",
    "LLM_COST_USD_TOTAL",
    "PROMETHEUS_AVAILABLE",
    "RAG_CACHE_HITS_TOTAL",
    "RAG_CACHE_MISSES_TOTAL",
    "MetricsRegistry",
    "get_metrics",
    "prometheus_enabled",
    "record_agent_result",
    "record_handoff",
    "record_llm_cost",
    "record_rag_cache",
    "render_prometheus_text",
]
