"""Feature 3: Prometheus /metrics endpoint + boas_* business counters.

These tests must stay hermetic: no Prometheus, no Grafana, no database. The
/metrics endpoint renders the in-process registry as plain text, so a bare
``TestClient`` (lifespan not started) is enough.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, CollectorRegistry

from apps.api.main import create_app
from packages.observability import metrics as boas_metrics
from packages.observability.metrics import (
    get_metrics,
    prometheus_enabled,
    record_agent_result,
    record_handoff,
    record_llm_cost,
    record_rag_cache,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """App under test.

    Deliberately not used as a context manager: lifespan would require settings
    / DB wiring that scraping metrics does not need.
    """
    return TestClient(create_app())


def _sample(name: str, **labels: str) -> float:
    """Current value of a prometheus sample (0.0 when not yet observed)."""
    value = REGISTRY.get_sample_value(name, labels or None)
    return 0.0 if value is None else float(value)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    # Instrumentation proof: prometheus_client default collectors are present.
    assert "python_info" in body or "python_gc_objects_collected_total" in body
    # Business metrics are registered even before any agent has run.
    assert "boas_agent_success_total" in body
    assert "boas_llm_cost_usd_total" in body
    assert "boas_rag_cache_hits_total" in body
    assert "boas_rag_cache_misses_total" in body
    assert "boas_handoff_total" in body


def test_metrics_endpoint_needs_no_api_key(client: TestClient) -> None:
    """/metrics sits outside /v1 so auth + rate limiting do not apply."""
    assert client.get("/metrics").status_code == 200


def test_http_instrumentation_records_requests(client: TestClient) -> None:
    assert client.get("/health").status_code == 200

    body = client.get("/metrics").text
    assert any(
        family in body
        for family in (
            "http_requests_total",
            "http_request_duration_seconds",
            "http_request_duration_highr_seconds",
        )
    )


def test_existing_health_route_still_works(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "business-ops-api"}


# ---------------------------------------------------------------------------
# record_* helpers: both sinks
# ---------------------------------------------------------------------------


def test_prometheus_business_counters_available() -> None:
    assert prometheus_enabled() is True


def test_record_agent_result_updates_both_sinks() -> None:
    agent = _unique("test.agent")
    labels = {"agent": agent, "domain": "sales", "status": "success"}
    before = _sample("boas_agent_success_total", **labels)

    record_agent_result(agent, "sales", "success", duration_s=0.42)

    assert _sample("boas_agent_success_total", **labels) == before + 1.0
    snapshot = get_metrics().snapshot()
    key = f"agent_runs_total|agent={agent},domain=sales,status=success"
    assert snapshot["counters"][key] == 1.0
    timing_key = f"agent_duration_seconds|agent={agent},domain=sales"
    assert snapshot["timings"][timing_key]["count"] == 1


def test_record_llm_cost_accumulates_usd() -> None:
    model = _unique("test-model")
    labels = {"model": model, "tag": "unit-test"}
    before = _sample("boas_llm_cost_usd_total", **labels)

    record_llm_cost(model, 0.25, tag="unit-test")
    record_llm_cost(model, 0.5, tag="unit-test")

    assert _sample("boas_llm_cost_usd_total", **labels) == pytest.approx(before + 0.75)
    key = f"llm_cost_usd_total|model={model},tag=unit-test"
    assert get_metrics().snapshot()["counters"][key] == pytest.approx(0.75)


def test_record_llm_cost_defaults_untagged_and_ignores_negative() -> None:
    model = _unique("test-model")
    record_llm_cost(model, -1.0)

    assert _sample("boas_llm_cost_usd_total", model=model, tag="untagged") == 0.0


def test_record_rag_cache_tracks_hits_and_misses() -> None:
    hits_before = _sample("boas_rag_cache_hits_total")
    misses_before = _sample("boas_rag_cache_misses_total")

    record_rag_cache(True)
    record_rag_cache(False)
    record_rag_cache(False)

    assert _sample("boas_rag_cache_hits_total") == hits_before + 1.0
    assert _sample("boas_rag_cache_misses_total") == misses_before + 2.0
    counters = get_metrics().snapshot()["counters"]
    assert counters["rag_cache_total|outcome=hit"] >= 1.0
    assert counters["rag_cache_total|outcome=miss"] >= 2.0


def test_record_handoff_counts_agent_pairs() -> None:
    src = _unique("test.from")
    dst = _unique("test.to")
    labels = {"from_agent": src, "to_agent": dst}

    record_handoff(src, dst)

    assert _sample("boas_handoff_total", **labels) == 1.0
    key = f"handoff_total|from_agent={src},to_agent={dst}"
    assert get_metrics().snapshot()["counters"][key] == 1.0


def test_helpers_degrade_gracefully_without_prometheus(monkeypatch) -> None:
    """Missing prometheus counters must never break the in-process registry."""
    for name in (
        "AGENT_SUCCESS_TOTAL",
        "LLM_COST_USD_TOTAL",
        "RAG_CACHE_HITS_TOTAL",
        "RAG_CACHE_MISSES_TOTAL",
        "HANDOFF_TOTAL",
    ):
        monkeypatch.setattr(boas_metrics, name, None)

    agent = _unique("test.degraded")
    record_agent_result(agent, "support", "failed")
    record_llm_cost(_unique("degraded-model"), 1.0, tag="degraded")
    record_rag_cache(True)
    record_handoff(agent, "agents.knowledge")

    key = f"agent_runs_total|agent={agent},domain=support,status=failed"
    assert get_metrics().snapshot()["counters"][key] == 1.0


def test_counter_factory_reuses_existing_registration(monkeypatch) -> None:
    """Duplicate registration (repeated app factories) must not raise."""
    fresh = CollectorRegistry()
    monkeypatch.setattr(boas_metrics, "PROMETHEUS_REGISTRY", fresh)

    first = boas_metrics._counter("boas_unit_probe_total", "probe", ("kind",))
    second = boas_metrics._counter("boas_unit_probe_total", "probe", ("kind",))

    assert first is not None
    assert first is second


def test_render_prometheus_text_includes_business_metrics() -> None:
    record_agent_result(_unique("test.render"), "reporting", "success")

    text = boas_metrics.render_prometheus_text()

    assert "boas_agent_success_total" in text