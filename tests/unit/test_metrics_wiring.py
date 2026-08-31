"""F13 regression guard: the boas_* counters must be fed by REAL app code.

``tests/unit/test_prometheus_metrics.py`` proves the ``record_*`` helpers work
when called directly. These tests prove the production call sites (classic
orchestrator, graph orchestrator, llm_cost ledger) actually call them -
otherwise the Grafana panels stay flat at 0 in production while the unit suite
stays green (the dead-metrics trap, finding F13).

Fully offline and hermetic: MockLLMProvider + stub agents, no DB, no network.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from packages.config.settings import LLMProviderKind, Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    TaskContext,
    TaskRequest,
)
from packages.core.bootstrap import build_container
from packages.core.graph import GraphOrchestrator
from packages.core.llm_cost import log_llm_usage
from packages.core.orchestrator import Orchestrator
from packages.core.registry import InMemoryAgentRegistry
from packages.llm.mock import MockLLMProvider

AGENT_COUNTER = "boas_agent_success_total"
HANDOFF_COUNTER = "boas_handoff_total"
COST_COUNTER = "boas_llm_cost_usd_total"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sample(name: str, **labels: str) -> float:
    """Value of one labelled prometheus sample (0.0 when never observed)."""
    value = REGISTRY.get_sample_value(name, labels or None)
    return 0.0 if value is None else float(value)


def _family_total(name: str) -> float:
    """Sum every label combination of a counter (label-agnostic delta check)."""
    total = 0.0
    for metric in REGISTRY.collect():
        for series in metric.samples:
            if series.name == name:
                total += series.value
    return total


def _mock_settings(*, langgraph: bool = False) -> Settings:
    return Settings(llm_provider=LLMProviderKind.MOCK, langgraph_enabled=langgraph)


def _support_request(**payload) -> TaskRequest:
    return TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload=payload,
        context=TaskContext(organization_id=uuid4()),
    )


class _StubAgent:
    """Deterministic DomainAgent stub: no DB, no LLM, optional handoff request."""

    def __init__(
        self,
        name: str,
        domain: Domain,
        capability: str,
        *,
        handoff_to: str | None = None,
    ) -> None:
        self.descriptor = AgentDescriptor(
            name=name, domain=domain, version="1", capabilities=[capability]
        )
        self._handoff_to = handoff_to

    async def handle(self, request: TaskRequest) -> AgentResponse:
        metadata = {"handoff": {"target_capability": self._handoff_to}} if self._handoff_to else {}
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={"ok": True},
            confidence=1.0,
            metadata=metadata,
        )


def _handoff_registry() -> tuple[InMemoryAgentRegistry, _StubAgent, _StubAgent]:
    """support.triage -> knowledge.query, both stubbed."""
    source = _StubAgent(
        "probe-support", Domain.SUPPORT, "support.triage", handoff_to="knowledge.query"
    )
    target = _StubAgent("probe-knowledge", Domain.KNOWLEDGE, "knowledge.query")
    registry = InMemoryAgentRegistry()
    registry.register(source.descriptor, source)
    registry.register(target.descriptor, target)
    return registry, source, target


@pytest.fixture()
def classic_container():
    return build_container(_mock_settings())


@pytest.fixture()
def graph_container():
    return build_container(_mock_settings(langgraph=True))


# ---------------------------------------------------------------------------
# boas_agent_success_total
# ---------------------------------------------------------------------------


async def test_orchestrator_run_increments_agent_counter(classic_container) -> None:
    before = _family_total(AGENT_COUNTER)

    resp = await classic_container.orchestrator.execute(
        _support_request(subject="Probe", body="Probe")
    )

    # Exactly one finalized AgentResponse -> exactly one counter increment.
    assert _family_total(AGENT_COUNTER) == before + 1.0
    assert (
        _sample(
            AGENT_COUNTER,
            agent=resp.agent,
            domain=Domain.SUPPORT.value,
            status=resp.status.value,
        )
        >= 1.0
    )


async def test_graph_orchestrator_run_increments_agent_counter(graph_container) -> None:
    before = _family_total(AGENT_COUNTER)

    resp = await graph_container.orchestrator.execute(_support_request())

    assert _family_total(AGENT_COUNTER) == before + 1.0
    assert (
        _sample(
            AGENT_COUNTER,
            agent=resp.agent,
            domain=Domain.SUPPORT.value,
            status=resp.status.value,
        )
        >= 1.0
    )


async def test_failed_task_increments_counter_with_failed_status(
    classic_container,
) -> None:
    """A permanent failure is a finalized response too (status=failed panel)."""
    labels = {
        "agent": "orchestrator",
        "domain": Domain.SUPPORT.value,
        "status": "failed",
    }
    before = _sample(AGENT_COUNTER, **labels)

    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="not_a_capability",
        payload={"subject": "Probe"},
        context=TaskContext(organization_id=uuid4()),
    )
    resp = await classic_container.orchestrator.execute(req)

    assert resp.status == AgentResponseStatus.FAILED
    assert _sample(AGENT_COUNTER, **labels) == before + 1.0


# ---------------------------------------------------------------------------
# boas_handoff_total
# ---------------------------------------------------------------------------


async def test_handoff_increments_handoff_counter() -> None:
    registry, source, target = _handoff_registry()
    orchestrator = Orchestrator(registry, MockLLMProvider())
    labels = {
        "from_agent": source.descriptor.qualified_name,
        "to_agent": target.descriptor.qualified_name,
    }
    handoffs_before = _sample(HANDOFF_COUNTER, **labels)
    agents_before = _family_total(AGENT_COUNTER)

    resp = await orchestrator.execute(_support_request())

    assert resp.metadata["handoff"]["to"] == target.descriptor.qualified_name
    assert _sample(HANDOFF_COUNTER, **labels) == handoffs_before + 1.0
    # Two finalized responses: the knowledge hop + the merged support response.
    assert _family_total(AGENT_COUNTER) == agents_before + 2.0
    assert (
        _sample(
            AGENT_COUNTER,
            agent=target.descriptor.qualified_name,
            domain=Domain.KNOWLEDGE.value,
            status="success",
        )
        == 1.0
    )


async def test_graph_handoff_increments_handoff_counter() -> None:
    """The LangGraph path must feed the same counters as the classic path."""
    registry, source, target = _handoff_registry()
    orchestrator = GraphOrchestrator(
        registry, MockLLMProvider(), settings=_mock_settings(langgraph=True)
    )
    labels = {
        "from_agent": source.descriptor.qualified_name,
        "to_agent": target.descriptor.qualified_name,
    }
    handoffs_before = _sample(HANDOFF_COUNTER, **labels)
    agents_before = _family_total(AGENT_COUNTER)

    resp = await orchestrator.execute(_support_request())

    assert resp.metadata["handoff"]["to"] == target.descriptor.qualified_name
    assert _sample(HANDOFF_COUNTER, **labels) == handoffs_before + 1.0
    assert _family_total(AGENT_COUNTER) == agents_before + 2.0


# ---------------------------------------------------------------------------
# boas_llm_cost_usd_total
# ---------------------------------------------------------------------------


def test_log_llm_usage_increments_cost_counter(tmp_path, monkeypatch) -> None:
    import packages.core.llm_cost as lc

    monkeypatch.setattr(lc, "_LEDGER", tmp_path / "usage.jsonl")
    model = f"cloud-probe-{uuid4().hex[:8]}"  # unpriced -> cloud fallback estimate

    rec = log_llm_usage(model, "x" * 4000, "y" * 2000, 1.5, tag="f13-probe")

    assert rec["est_cost_usd"] > 0.0
    assert _sample(COST_COUNTER, model=model, tag="f13-probe") == pytest.approx(rec["est_cost_usd"])


def test_cache_hit_is_recorded_as_zero_spend(tmp_path, monkeypatch) -> None:
    """A cache hit never reached the provider: series exists, spend stays 0."""
    import packages.core.llm_cost as lc

    monkeypatch.setattr(lc, "_LEDGER", tmp_path / "usage.jsonl")
    model = f"cloud-probe-{uuid4().hex[:8]}"

    log_llm_usage(model, "x" * 4000, "y" * 2000, 0.0, cache_hit=True, tag="f13-probe")

    assert _sample(COST_COUNTER, model=model, tag="f13-probe") == 0.0


# ---------------------------------------------------------------------------
# telemetry must never break the business flow
# ---------------------------------------------------------------------------


async def test_metrics_failure_never_breaks_the_task(monkeypatch) -> None:
    from packages.core import orchestrator as orchestrator_module

    def boom(**_kwargs):
        raise RuntimeError("prometheus exploded")

    monkeypatch.setattr(orchestrator_module, "record_agent_result", boom)
    monkeypatch.setattr(orchestrator_module, "record_handoff", boom)

    registry, source, target = _handoff_registry()
    orchestrator = Orchestrator(registry, MockLLMProvider())

    resp = await orchestrator.execute(_support_request())

    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.metadata["handoff"]["to"] == target.descriptor.qualified_name


def test_llm_cost_metric_failure_never_breaks_the_ledger(tmp_path, monkeypatch) -> None:
    import packages.core.llm_cost as lc

    def boom(**_kwargs):
        raise RuntimeError("prometheus exploded")

    ledger = tmp_path / "usage.jsonl"
    monkeypatch.setattr(lc, "_LEDGER", ledger)
    monkeypatch.setattr(lc, "_record_llm_cost", boom)

    rec = log_llm_usage("qwen3:1.7b", "prompt", "answer", 0.1, tag="f13-probe")

    assert rec["in_tokens"] > 0
    assert ledger.read_text(encoding="utf-8").strip().endswith("}")
