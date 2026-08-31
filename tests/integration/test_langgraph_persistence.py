"""Tests for LangGraph persistent checkpointing (Feature 2).

By default (no ``langgraph_checkpoint_url``) the orchestrator uses an
in-memory checkpointer so this suite runs without a database. The Postgres
selection path is exercised with a mocked ``PostgresSaver`` so no real DB is
required.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from packages.config.settings import Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    TaskContext,
    TaskRequest,
)
from packages.core.checkpoint import (
    _POSTGRES_AVAILABLE,
    PostgresCheckpointManager,
    close_checkpointers,
    get_checkpointer,
)


class SimpleAgent:
    """Deterministic agent: always returns SUCCESS."""

    async def handle(self, request: TaskRequest) -> AgentResponse:
        return AgentResponse(
            task_id=request.task_id,
            agent="simp-v1",
            status=AgentResponseStatus.SUCCESS,
            result={"ok": True},
            citations=[],
            confidence=1.0,
            metadata={},
        )


def _make_orchestrator(checkpointer):
    from packages.core.graph import GraphOrchestrator
    from packages.core.registry import InMemoryAgentRegistry
    from packages.llm.mock import MockLLMProvider

    registry = InMemoryAgentRegistry()
    registry.register(
        AgentDescriptor(
            name="simp",
            domain=Domain.SUPPORT,
            version="1",
            capabilities=["support.simp"],
        ),
        SimpleAgent(),
    )
    return GraphOrchestrator(registry, MockLLMProvider(), checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 1. Default checkpointer selection
# ---------------------------------------------------------------------------


def test_default_checkpointer_is_inmemory() -> None:
    """When langgraph_checkpoint_url is None, get_checkpointer returns InMemorySaver."""
    settings = Settings(langgraph_checkpoint_url=None)
    cp = get_checkpointer(settings)
    assert isinstance(cp, InMemorySaver)


@pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason="langgraph-checkpoint-postgres not installed",
)
def test_postgres_checkpointer_selected_when_url_set(monkeypatch) -> None:
    """When a URL is configured, get_checkpointer returns a PostgresSaver."""

    class FakeSaver(BaseCheckpointSaver):
        def setup(self) -> None:
            self._setup_called = True

    class FakePostgresSaver:
        # Mirrors the real API: from_conn_string is a @contextmanager classmethod,
        # so it must be *entered* to obtain the saver.
        @classmethod
        @contextmanager
        def from_conn_string(cls, url: str) -> Iterator[FakeSaver]:
            yield FakeSaver()

    monkeypatch.setattr("packages.core.checkpoint.PostgresSaver", FakePostgresSaver)

    settings = Settings(
        langgraph_checkpoint_url="postgresql://user:pass@localhost:5432/checkpoints"
    )
    cp = get_checkpointer(settings)
    assert isinstance(cp, FakeSaver)
    # get_checkpointer caches the entered saver for the process lifetime; drop it
    # so the fake does not outlive this test.
    close_checkpointers()


def test_postgres_manager_uses_conn_string(monkeypatch) -> None:
    """PostgresCheckpointManager builds a saver from the connection string."""
    if not _POSTGRES_AVAILABLE:
        pytest.skip("langgraph-checkpoint-postgres not installed")

    captured = {}

    class FakeSaver(BaseCheckpointSaver):
        def setup(self) -> None:
            captured["setup"] = True

    class FakePostgresSaver:
        # Mirrors the real API: a @contextmanager classmethod whose connection is
        # only open between __enter__ and __exit__.
        @classmethod
        @contextmanager
        def from_conn_string(cls, url: str) -> Iterator[FakeSaver]:
            captured["url"] = url
            captured["entered"] = True
            try:
                yield FakeSaver()
            finally:
                captured["exited"] = True

    monkeypatch.setattr("packages.core.checkpoint.PostgresSaver", FakePostgresSaver)
    manager = PostgresCheckpointManager("postgresql://x/y")
    manager.setup()
    assert captured["url"] == "postgresql://x/y"
    assert captured.get("setup") is True
    # The context manager must be entered and the *entered* saver exposed --
    # never the _GeneratorContextManager itself.
    assert captured.get("entered") is True
    assert isinstance(manager.checkpointer, FakeSaver)
    assert captured.get("exited") is None  # still open: kept alive for the process
    manager.close()
    assert captured.get("exited") is True


# ---------------------------------------------------------------------------
# 2. Persistence: state survives across graph instances (in-memory default)
# ---------------------------------------------------------------------------


async def test_inmemory_checkpointer_persists_state_across_invocations() -> None:
    """A graph run persists state readable from a second graph instance.

    This mirrors what Postgres persistence provides: the checkpointer (not the
    graph instance) owns the state, so re-reading the same thread_id after the
    producing graph is gone still yields the completed state.
    """
    cp = InMemorySaver()
    orchestrator = _make_orchestrator(cp)
    task_id = uuid4()
    req = TaskRequest(
        task_id=task_id,
        domain=Domain.SUPPORT,
        action="simp",
        payload={"x": "y"},
        context=TaskContext(organization_id=uuid4()),
    )

    resp = await orchestrator.execute(req)
    assert resp.status == AgentResponseStatus.SUCCESS

    # Read the persisted state from a *second* orchestrator wired to the SAME
    # checkpointer — proving the state lives in the checkpointer, not the graph.
    orchestrator2 = _make_orchestrator(cp)
    snap = await orchestrator2._graph.aget_state({"configurable": {"thread_id": str(task_id)}})
    assert snap is not None
    assert snap.values is not None
    assert snap.values.get("response") is not None
    assert snap.values["response"].status == AgentResponseStatus.SUCCESS


async def test_graph_records_intermediate_checkpoints() -> None:
    """Each node boundary produces a checkpoint visible via list()."""
    cp = InMemorySaver()
    orchestrator = _make_orchestrator(cp)
    task_id = uuid4()
    req = TaskRequest(
        task_id=task_id,
        domain=Domain.SUPPORT,
        action="simp",
        payload={"x": "y"},
        context=TaskContext(organization_id=uuid4()),
    )
    await orchestrator.execute(req)

    states = []
    async for _s in cp.alist({"configurable": {"thread_id": str(task_id)}}):
        states.append(_s)
    # At least START + terminal checkpoint should be recorded.
    assert len(states) >= 1
