"""Task persistence boundary contracts (Phase 1).

The orchestrator and API routes depend on `TaskRecorder` and `TaskStore`
protocols — never on SQLAlchemy directly. This keeps the core decoupled from
the DB (ADR-005 style) and lets tests inject in-memory fakes that are fully
deterministic without a live PostgreSQL/Neon instance (ADR-001).

Contracts
---------
- `TaskRecorder`: called by the orchestrator after each `TaskStatus`
  transition so the durable `tasks` / `task_steps` rows track progress.
- `TaskStore`: called by ``POST /v1/tasks`` for idempotency resolution,
  final result write and rollback.

No-op implementations are the CI/dev default (`persistence_enabled=False`),
so the existing Phase 0 test suite keeps passing without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from packages.contracts.enums import TaskStatus
from packages.contracts.models import AgentResponse, TaskRequest

# ---------------------------------------------------------------------------
# Transition recorder (consumed by the orchestrator)
# ---------------------------------------------------------------------------


class TaskRecorder(Protocol):
    """Records durable lifecycle transitions for one task."""

    async def record_transition(self, task_id: UUID | str, status: TaskStatus) -> None: ...


class NoopTaskRecorder:
    """No-op recorder — the application still runs fully without a DB."""

    async def record_transition(self, task_id: UUID | str, status: TaskStatus) -> None:
        return None


# ---------------------------------------------------------------------------
# Task persistence store (consumed by the API route)
# ---------------------------------------------------------------------------


@dataclass
class TaskResolution:
    """Outcome of resolving a task against durable storage.

    - ``created=True``: the task row was created (PENDING); the orchestrator
      should run the task.
    - ``created=False, response=<AgentResponse>``: the task already reached a
      terminal state; the stored response should be replayed without re-running.
    - ``created=False, response=None`` is never returned — an in-flight task
      raises ``TaskStateError`` (409).
    """

    created: bool
    response: AgentResponse | None = None


class TaskStore(Protocol):
    """Durable storage used by the task API route."""

    async def resolve(self, request: TaskRequest) -> TaskResolution: ...
    async def complete(self, task_id: UUID | str, response: AgentResponse) -> None: ...
    async def rollback(self) -> None: ...
    async def list_tasks(self, status: TaskStatus | None = None) -> list[dict]: ...
    async def get_task(self, task_id: UUID | str) -> dict | None: ...
    async def list_steps(self, correlation_id: str | None = None) -> list[dict]: ...


class NoopTaskStore:
    """No-op store — no rows are written (CI/dev default)."""

    async def resolve(self, request: TaskRequest) -> TaskResolution:
        return TaskResolution(created=True)

    async def complete(self, task_id: UUID | str, response: AgentResponse) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def list_tasks(self, status: TaskStatus | None = None) -> list[dict]:
        return []

    async def get_task(self, task_id: UUID | str) -> dict | None:
        return None

    async def list_steps(self, task_id: str | None = None) -> list[dict]:
        return []

    # Also satisfies TaskRecorder so routes can pass it to the orchestrator.
    async def record_transition(self, task_id: UUID | str, status: TaskStatus) -> None:
        return None


__all__ = [
    "NoopTaskRecorder",
    "NoopTaskStore",
    "TaskRecorder",
    "TaskResolution",
    "TaskStore",
]