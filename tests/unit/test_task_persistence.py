"""Phase 1 Item 1 - task/persistence lifecycle wiring.

Covers:
- idempotent replay: a repeated task_id returns the stored response without
  creating a second row or re-running the orchestrator;
- crash mid-flight: orchestration that dies between transitions leaves the row
  at a valid intermediate status (never stuck at PENDING).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.routes.tasks import get_task_store
from packages.contracts.enums import AgentResponseStatus, Domain, TaskStatus
from packages.contracts.models import AgentDescriptor, AgentResponse, TaskRequest
from packages.core.errors import TaskStateError
from packages.core.orchestrator import Orchestrator
from packages.core.persistence import TaskResolution
from packages.core.registry import InMemoryAgentRegistry
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


def test_post_tasks_idempotent_replay() -> None:
    app = create_app()
    fake = _MemTaskStore()
    app.dependency_overrides[get_task_store] = lambda: fake

    client = TestClient(app)
    body = {
        "domain": "knowledge",
        "action": "query",
        "payload": {"question": "refund policy?"},
        "task_id": str(uuid4()),
    }
    first = client.post("/v1/tasks", json=body)
    second = client.post("/v1/tasks", json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert fake.created_count == 1  # replay must not create a new row


def test_post_tasks_inflight_conflict() -> None:
    app = create_app()
    fake = _MemTaskStore()
    tid = uuid4()
    fake._rows[str(tid)] = _MemRecord(status=TaskStatus.RUNNING)
    app.dependency_overrides[get_task_store] = lambda: fake
    client = TestClient(app)
    resp = client.post(
        "/v1/tasks",
        json={
            "domain": "support",
            "action": "triage",
            "payload": {"subject": "s"},
            "task_id": str(tid),
        },
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# SQLAlchemy-backed store (sqlite + aiosqlite, CI-runnable without Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def sqlite_store(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.database import models
    from packages.database.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[models.Task.__table__, models.TaskStep.__table__],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield SqlAlchemyTaskStore(session)
    await engine.dispose()


async def test_store_persists_and_replays(sqlite_store) -> None:
    req = TaskRequest(domain="knowledge", action="query", payload={"question": "hi"})
    resolution = await sqlite_store.resolve(req)
    assert resolution.created is True

    resp = AgentResponse(
        task_id=req.task_id,
        agent="knowledge-v1",
        status=AgentResponseStatus.SUCCESS,
        result={"answer": "hello"},
    )
    await sqlite_store.complete(req.task_id, resp)

    fetched = await sqlite_store.get_task(req.task_id)
    assert fetched is not None
    assert fetched["status"] == "completed"

    again = await sqlite_store.resolve(req)
    assert again.created is False
    assert again.response is not None
    assert again.response.result == {"answer": "hello"}


async def test_store_records_transitions(sqlite_store) -> None:
    req = TaskRequest(domain="support", action="triage", payload={})
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


async def test_orchestrator_crash_leaves_valid_intermediate() -> None:
    registry = InMemoryAgentRegistry()
    registry.register(_CrashAgent.descriptor, _CrashAgent())
    orch = Orchestrator(registry, MockLLMProvider())
    recorder = _CollectRecorder()
    req = TaskRequest(domain="knowledge", action="crash", payload={})

    with pytest.raises(RuntimeError):
        await orch.execute(req, recorder=recorder)

    assert recorder.events[:3] == [
        TaskStatus.CLASSIFYING,
        TaskStatus.ROUTING,
        TaskStatus.RUNNING,
    ]
    last = recorder.events[-1]
    assert last == TaskStatus.RUNNING
    assert last not in _TERMINAL | {TaskStatus.PENDING}
    async def list_steps(self, task_id: str | None = None) -> list[dict]:
        return []

    async def record_transition(self, task_id, status: TaskStatus) -> None:
        return None