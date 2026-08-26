"""SQLAlchemy-backed task persistence (Phase 1).

Maps agent orchestration progress onto the existing Phase 0 tables — the
``tasks`` row tracks status + final ``AgentResponse`` (stored as JSON in
``tasks.result``) and each transition appends a ``task_steps`` row carrying a
``correlation_id``. No new result tables are created (Item 1.4).

Transaction semantics
---------------------
Recorded transitions are flushed progressively within one session transaction
so intermediate state is visible; the route calls :meth:`complete` to write the
final response and commit atomically, or :meth:`rollback` on failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.enums import AgentResponseStatus, TaskStatus
from packages.contracts.models import AgentResponse, TaskRequest
from packages.core.errors import TaskStateError
from packages.core.persistence import TaskResolution
from packages.database.models import Task, TaskStatusDB, TaskStep, TaskStepStatus
from packages.observability.context import get_context


def _task_to_dict(task: Task) -> dict:
    return {
        "task_id": str(task.id),
        "domain": task.domain,
        "action": task.action,
        "status": task.status.value,
        "payload": task.payload,
        "result": task.result,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "correlation_id": task.correlation_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }

# Terminal DB statuses used to build the idempotency replay path.
_TERMINAL_DB = frozenset(
    {
        TaskStatusDB.completed,
        TaskStatusDB.failed,
        TaskStatusDB.escalated,
        TaskStatusDB.cancelled,
    }
)

# Agent terminal status -> durable task status.
_AGENT_TO_TASK = {
    AgentResponseStatus.SUCCESS: TaskStatusDB.completed,
    AgentResponseStatus.FAILED: TaskStatusDB.failed,
    AgentResponseStatus.TIMEOUT: TaskStatusDB.failed,
    AgentResponseStatus.REJECTED: TaskStatusDB.failed,  # no dedicated DB task state
    AgentResponseStatus.ESCALATED: TaskStatusDB.escalated,
}


def _to_db_status(status: TaskStatus | None) -> TaskStatusDB | None:
    """Convert a contracts TaskStatus to the DB enum (shared string values)."""
    return TaskStatusDB(status.value) if status is not None else None


def _response_from_task(task: Task) -> AgentResponse:
    """Rebuild the canonical response from a stored task row."""
    data = dict(task.result or {})
    data["task_id"] = task.id
    return AgentResponse.model_validate(data)


class SqlAlchemyTaskStore:
    """Persists task lifecycle using a shared ``AsyncSession``.

    Also satisfies the ``TaskRecorder`` protocol, so the API route passes the
    same object to the orchestrator and the store handles finalize + commit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # TaskStore
    # ------------------------------------------------------------------

    async def resolve(self, request: TaskRequest) -> TaskResolution:
        task = await self._session.get(Task, request.task_id)
        if task is None:
            self._session.add(
                Task(
                    id=request.task_id,
                    organization_id=request.context.organization_id,
                    domain=request.domain.value,
                    action=request.action,
                    status=TaskStatusDB.pending,
                    payload=request.payload,
                    correlation_id=get_context().request_id,
                )
            )
            await self._session.flush()
            return TaskResolution(created=True)

        if task.status in _TERMINAL_DB:
            # Idempotent replay: caller repeats an already-finished task_id.
            return TaskResolution(created=False, response=_response_from_task(task))

        raise TaskStateError(
            "Task already in flight",
            task_id=request.task_id,
            details={"status": task.status.value},
        )

    async def complete(self, task_id: UUID | str, response: AgentResponse) -> None:
        task = await self._session.get(Task, task_id)
        if task is None:
            return
        task.status = _AGENT_TO_TASK[response.status]
        task.result = response.model_dump(mode="json", exclude_none=True)
        if response.error is not None:
            task.error_code = response.error.code
            task.error_message = response.error.message
        await self._session.flush()
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        *,
        organization_id: UUID | None = None,
    ) -> list[dict]:
        stmt = select(Task).order_by(Task.created_at.desc())
        if status is not None:
            stmt = stmt.where(Task.status == _to_db_status(status))
        if organization_id is not None:
            stmt = stmt.where(Task.organization_id == organization_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_task_to_dict(t) for t in rows]

    async def get_task(
        self,
        task_id: UUID | str,
        *,
        organization_id: UUID | None = None,
    ) -> dict | None:
        task = await self._session.get(Task, task_id)
        if task is None:
            return None
        if (
            organization_id is not None
            and task.organization_id != organization_id
        ):
            return None  # cross-tenant access: behave as not-found
        return _task_to_dict(task)

    async def list_steps(
        self,
        task_id: str | None = None,
        *,
        correlation_id: str | None = None,
        limit: int = 200,
        organization_id: UUID | None = None,
    ) -> list[dict]:
        # Per-task view: chronological by step sequence. Global audit view:
        # most recent first.
        if task_id is not None:
            tid = task_id if isinstance(task_id, UUID) else UUID(task_id)
            stmt = (
                select(TaskStep)
                .join(Task, Task.id == TaskStep.task_id)
                .where(TaskStep.task_id == tid)
                .order_by(TaskStep.sequence)
            )
            if organization_id is not None:
                stmt = stmt.where(Task.organization_id == organization_id)
        else:
            stmt = select(TaskStep).order_by(TaskStep.started_at.desc())
            if organization_id is not None:
                stmt = stmt.join(Task, Task.id == TaskStep.task_id).where(
                    Task.organization_id == organization_id
                )
        if correlation_id is not None:
            stmt = stmt.where(TaskStep.correlation_id == correlation_id)
        rows = ((await self._session.execute(stmt.limit(limit))).scalars().all())
        return [
            {
                "id": str(s.id),
                "task_id": str(s.task_id),
                "sequence": s.sequence,
                "name": s.name,
                "status": s.status.value,
                "output": s.output,
                "correlation_id": s.correlation_id,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
            }
            for s in rows
        ]

    # ------------------------------------------------------------------
    # TaskRecorder (appends step rows + advances task status)
    # ------------------------------------------------------------------

    async def record_transition(self, task_id: UUID | str, status: TaskStatus) -> None:
        task = await self._session.get(Task, task_id)
        if task is None:
            return
        task.status = _to_db_status(status) or task.status
        seq = await _next_sequence(self._session, task_id)
        now = datetime.now(UTC)
        self._session.add(
            TaskStep(
                task_id=task_id,
                sequence=seq,
                name=status.value,
                status=TaskStepStatus.succeeded,
                output={"task_id": str(task_id), "status": status.value},
                correlation_id=get_context().request_id,
                started_at=now,
                finished_at=now,
            )
        )
        await self._session.flush()


async def _next_sequence(session: AsyncSession, task_id: UUID | str) -> int:
    """Return the next step sequence for a task (max existing + 1)."""
    from sqlalchemy import func

    result = await session.execute(
        select(func.max(TaskStep.sequence)).where(TaskStep.task_id == task_id)
    )
    max_seq = result.scalar_one()
    return (max_seq or 0) + 1


__all__ = ["SqlAlchemyTaskStore"]