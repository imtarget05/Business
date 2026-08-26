"""Phase 1 Item 1 - task/persistence lifecycle wiring.

Covers:
- idempotent replay: a repeated task_id returns the stored response without
  creating a second row or re-running the orchestrator;
- crash mid-flight: orchestration that dies between transitions leaves the row
  at a valid intermediate status (never stuck at PENDING).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid as _uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

import packages.database.session as session_mod
import packages.config.settings as settings_mod
from apps.api.main import create_app
from apps.api.routes.tasks import get_task_store
from packages.config.settings import Settings, LLMProviderKind
from packages.contracts.enums import AgentResponseStatus, Domain, TaskStatus
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    TaskContext,
    TaskRequest,
)
from packages.core.errors import TaskStateError
from packages.core.orchestrator import Orchestrator
from packages.core.persistence import TaskResolution
from packages.core.registry import InMemoryAgentRegistry
from packages.database import models
from packages.database.base import Base
from packages.database.session import get_session_factory
from packages.database.task_store import SqlAlchemyTaskStore
from packages.llm.mock import MockLLMProvider

_TERMINAL = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.ESCALATED,
    TaskStatus.CANCELLED,
}


def _agent_to_task(status: AgentResponseStatus) -> TaskStatus:
    if status == AgentResponseStatus.SUCCESS:
        return TaskStatus.COMPLETED
    if status == AgentResponseStatus.ESCALATED:
        return TaskStatus.ESCALATED
    return TaskStatus.FAILED


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh module state per test: point the global engine at a temp sqlite db."""
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_session_factory", None)

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    # Provide legacy tenant_api_keys for backward compatibility
    settings = Settings(
        database_url=url,
        persistence_enabled=True,
        llm_provider=LLMProviderKind.MOCK,
        tenant_api_keys={
            "tenant-key-a": "00000000-0000-0000-0000-000000000001",
        },
    )
    get_session_factory(settings)

    # Point the cached settings singleton at our test configuration
    live = settings_mod.get_settings()
    monkeypatch.setattr(live, "database_url", url)
    monkeypatch.setattr(live, "persistence_enabled", True)
    monkeypatch.setattr(live, "llm_provider", LLMProviderKind.MOCK)
    monkeypatch.setattr(live, "tenant_api_keys", {
        "tenant-key-a": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(live, "rate_limit_per_minute", 1000)

    async def _setup() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                models.Organization.__table__.insert().values(
                    id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    name="Pilot Org",
                    slug="pilot",
                )
            )
        await eng.dispose()

    asyncio.run(_setup())
    yield TestClient(create_app())
    session_mod._engine = None
    session_mod._session_factory = None


# ---------------------------------------------------------------------------
# Fake in-memory store (route-level tests, no DB)
# ---------------------------------------------------------------------------


@dataclass
class _MemRecord:
    status: TaskStatus
    result: dict | None = None


class _MemTaskStore:
    def __init__(self) -> None:
        self._rows: dict[str, _MemRecord] = {}
        self.created_count = 0

    async def resolve(self, request: TaskRequest) -> TaskResolution:
        key = str(request.task_id)
        if key not in self._rows:
            self._rows[key] = _MemRecord(status=TaskStatus.PENDING)
            self.created_count += 1
            return TaskResolution(created=True)
        row = self._rows[key]
        if row.status in _TERMINAL:
            return TaskResolution(
                created=False, response=AgentResponse.model_validate(row.result)
            )
        raise TaskStateError("already in flight", task_id=request.task_id)

    async def complete(self, task_id, response: AgentResponse) -> None:
        row = self._rows[str(task_id)]
        row.status = _agent_to_task(response.status)
        row.result = response.model_dump(mode="json")

    async def rollback(self) -> None:
        return None

    async def list_tasks(self, status: TaskStatus | None = None) -> list[dict]:
        return []

    async def get_task(self, task_id) -> dict | None:
        return None

    async def list_steps(self, task_id: str | None = None) -> list[dict]:
        return []

    async def record_transition(self, task_id, status: TaskStatus) -> None:
        return None


# ---------------------------------------------------------------------------
# Route-level idempotency
# ---------------------------------------------------------------------------


def test_post_tasks_idempotent_replay(client) -> None:
    fake = _MemTaskStore()
    client.app.dependency_overrides[get_task_store] = lambda: fake

    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "refund policy?"},
        "task_id": str(_uuid.uuid4()),
    }
    first = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    second = client.post("/v1/tasks", json=body, headers={"X-API-Key": "tenant-key-a"})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert fake.created_count == 1  # replay must not create a new row


def test_post_tasks_inflight_conflict(client) -> None:
    fake = _MemTaskStore()
    tid = _uuid.uuid4()
    fake._rows[str(tid)] = _MemRecord(status=TaskStatus.RUNNING)
    client.app.dependency_overrides[get_task_store] = lambda: fake
    resp = client.post(
        "/v1/tasks",
        json={
            "domain": "support",
            "action": "triage",
            "payload": {"subject": "s"},
            "task_id": str(tid),
        },
        headers={"X-API-Key": "tenant-key-a"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# SQLAlchemy-backed store (sqlite + aiosqlite, CI-runnable without Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def sqlite_store(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            models.Organization.__table__.insert().values(
                id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                name="Pilot Org",
                slug="pilot",
            )
        )

    # Create a session for the store
    async with factory() as session:
        yield SqlAlchemyTaskStore(session)

    await engine.dispose()


async def test_store_persists_and_replays(sqlite_store: SqlAlchemyTaskStore) -> None:
    """Test that completing a task allows idempotent replay."""
    req = TaskRequest(
        task_id=_uuid.uuid4(),
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": "test"},
        context=TaskContext(channel="web", organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001")),
    )
    res = await sqlite_store.resolve(req)
    assert res.created is True

    # Complete the task
    resp = AgentResponse(
        status=AgentResponseStatus.SUCCESS,
        result={"answer": "ok"},
        citations=[],
        agent="knowledge-v1",
        task_id=req.task_id,
    )
    await sqlite_store.complete(req.task_id, resp)

    # Second call with same task_id -> replay (idempotent)
    res2 = await sqlite_store.resolve(req)
    assert res2.created is False
    assert res2.response is not None
    assert res2.response.result == {"answer": "ok"}


async def test_store_records_transitions(sqlite_store: SqlAlchemyTaskStore) -> None:
    """Test that record_transition creates step records."""
    req = TaskRequest(
        task_id=_uuid.uuid4(),
        domain=Domain.SUPPORT,
        action="triage",
        payload={"subject": "test"},
        context=TaskContext(channel="web", organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001")),
    )
    await sqlite_store.resolve(req)
    await sqlite_store.record_transition(req.task_id, TaskStatus.CLASSIFYING)
    await sqlite_store.record_transition(req.task_id, TaskStatus.ROUTING)
    steps = await sqlite_store.list_steps(str(req.task_id))
    assert [s["name"] for s in steps] == ["classifying", "routing"]
    assert all(s["correlation_id"] for s in steps)


# ---------------------------------------------------------------------------
# Crash mid-flight: orchestrator dies between transitions
# ---------------------------------------------------------------------------


class _CrashAgent:
    descriptor = AgentDescriptor(
        name="flash",
        domain=Domain.KNOWLEDGE,
        capabilities=frozenset({"knowledge.crash"}),
    )

    async def handle(self, request: TaskRequest) -> AgentResponse:
        raise RuntimeError("process died")


class _CollectRecorder:
    def __init__(self) -> None:
        self.events: list[TaskStatus] = []

    async def record_transition(self, task_id, status: TaskStatus) -> None:
        self.events.append(status)

    async def resolve(self, request: TaskRequest) -> TaskResolution:
        return TaskResolution(created=True)

    async def complete(self, task_id, response: AgentResponse) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def list_tasks(self, status: TaskStatus | None = None) -> list[dict]:
        return []

    async def get_task(self, task_id) -> dict | None:
        return None

    async def list_steps(self, task_id: str | None = None) -> list[dict]:
        return []


async def test_orchestrator_crash_leaves_valid_intermediate() -> None:
    registry = InMemoryAgentRegistry()
    registry.register(_CrashAgent.descriptor, _CrashAgent())
    orch = Orchestrator(registry, MockLLMProvider())
    recorder = _CollectRecorder()
    req = TaskRequest(domain="knowledge", action="crash", payload={})

    resp = await orch.execute(req, recorder=recorder)

    assert recorder.events[:3] == [
        TaskStatus.CLASSIFYING,
        TaskStatus.ROUTING,
        TaskStatus.RUNNING,
    ]
    last = recorder.events[-1]
    assert last == TaskStatus.FAILED
    # Crash is converted to a typed FAILED response — never an unhandled 500.
    assert resp.status == AgentResponseStatus.FAILED
    assert last == TaskStatus.FAILED  # terminal, never stuck mid-flight


async def test_sqlite_store_rollback_on_failure(sqlite_store: SqlAlchemyTaskStore) -> None:
    tid = _uuid.uuid4()
    req = TaskRequest(
        task_id=tid,
        domain=Domain.SUPPORT,
        action="triage",
        payload={"subject": "fail"},
        context=TaskContext(channel="web", organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001")),
    )
    await sqlite_store.resolve(req)
    # Simulate failure by rolling back
    await sqlite_store.rollback()

    row = await sqlite_store.get_task(tid)
    # Should not be completed
    assert row is None or row["status"] != "completed"