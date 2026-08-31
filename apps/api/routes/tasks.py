"""Task + agent API routes.

Phase 1: POST /v1/tasks now persists task lifecycle to the database when
`persistence_enabled` is true (see Settings). It:
  1. resolves the task_id against durable storage (idempotency),
  2. runs the orchestrator (recording each state transition as task_steps),
  3. writes the final AgentResponse to the task row in the same transaction,
  4. rolls back cleanly if orchestration fails.

When persistence is disabled (CI/dev default), a no-op store keeps Phase 0
behaviour — the orchestrator still runs and returns canonical responses.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org
from packages.config.settings import get_settings
from packages.contracts.enums import TaskStatus
from packages.contracts.models import TaskRequest
from packages.core.bootstrap import get_container
from packages.core.errors import NotFoundError, ValidationError
from packages.core.persistence import NoopTaskStore, TaskStore
from packages.database.models import AgentRun, AuditLog, Task, TaskStep
from packages.database.session import get_session
from packages.database.task_store import SqlAlchemyTaskStore

router = APIRouter(prefix="/v1")
# NOTE: authentication is enforced once by the X-API-Key middleware in
# apps/api/main.py for every /v1/* route — no per-route checks needed.


async def get_task_store(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> TaskStore:
    """FastAPI dependency: real DB store when enabled, noop otherwise."""
    if not get_settings().persistence_enabled:
        return NoopTaskStore()
    return SqlAlchemyTaskStore(db)


@router.post("/tasks")
async def create_task(
    request: TaskRequest,
    store: TaskStore = Depends(get_task_store),
    org_id=Depends(current_org),
) -> dict:
    container = get_container()
    # Server-side tenant binding: ignore any client-supplied organization.
    request.context.organization_id = org_id
    if not request.payload and request.action != "ping":
        raise ValidationError("payload must not be empty", task_id=request.task_id)

    resolution = await store.resolve(request)
    if resolution.response is not None:
        # Idempotent replay: task already reached a terminal state.
        return resolution.response.model_dump(mode="json")

    recorder = store  # SqlAlchemyTaskStore also implements TaskRecorder
    try:
        response = await container.orchestrator.execute(
            request, recorder=recorder, policy=container.policy
        )
    except Exception:
        await store.rollback()
        raise
    await store.complete(request.task_id, response)
    return response.model_dump(mode="json")


@router.get("/tasks")
async def list_tasks(
    request: Request,
    store: TaskStore = Depends(get_task_store),
    org_id=Depends(current_org),
    status: TaskStatus | None = None,
) -> dict:
    tasks = await store.list_tasks(status, organization_id=org_id)
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    request: Request,
    store: TaskStore = Depends(get_task_store),
    org_id=Depends(current_org),
) -> dict:
    task = await store.get_task(task_id, organization_id=org_id)
    if task is None:
        raise NotFoundError("Task not found", task_id=task_id)
    steps = await store.list_steps(task_id=str(task_id), organization_id=org_id)
    return {"task": task, "steps": steps}


@router.get("/tasks/{task_id}/timeline")
async def get_task_timeline(
    task_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id=Depends(current_org),
) -> dict:
    """Return a chronological timeline of events for a task.

    Events are assembled from:
    - Task creation and status changes (from task_steps)
    - Agent runs (from agent_runs)
    - Audit log entries (from audit_logs)

    All events are org-scoped - returns 404 if the task belongs to another org.
    """
    # Verify task exists and belongs to this org
    task = await db.get(Task, task_id)
    if task is None or task.organization_id != org_id:
        raise NotFoundError("Task not found", task_id=task_id)

    events: list[dict] = []

    # 1. Task creation event
    events.append(
        {
            "time": task.created_at,
            "stage": "task",
            "status": "created",
            "detail": f"Task created: {task.domain}.{task.action}",
        }
    )

    # 2. Task steps (status transitions)
    stmt_steps = select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.sequence)
    steps_result = await db.execute(stmt_steps)
    for step in steps_result.scalars().all():
        if step.started_at:
            events.append(
                {
                    "time": step.started_at,
                    "stage": "step",
                    "status": step.status.value,
                    "detail": f"Step {step.sequence}: {step.name}",
                }
            )
        if step.finished_at and step.finished_at != step.started_at:
            events.append(
                {
                    "time": step.finished_at,
                    "stage": "step",
                    "status": step.status.value,
                    "detail": f"Step {step.sequence}: {step.name} finished",
                }
            )

    # 3. Agent runs
    stmt_runs = (
        select(AgentRun)
        .where(AgentRun.task_id == task_id)
        .order_by(AgentRun.attempt, AgentRun.started_at)
    )
    runs_result = await db.execute(stmt_runs)
    for run in runs_result.scalars().all():
        if run.started_at:
            events.append(
                {
                    "time": run.started_at,
                    "stage": "agent_run",
                    "status": run.status,
                    "detail": f"Agent run attempt {run.attempt} started (agent: {run.agent_id})",
                }
            )
        if run.finished_at:
            events.append(
                {
                    "time": run.finished_at,
                    "stage": "agent_run",
                    "status": run.status,
                    "detail": f"Agent run attempt {run.attempt} finished: {run.status}",
                }
            )
        if run.error_code:
            events.append(
                {
                    "time": run.finished_at or run.started_at,
                    "stage": "agent_run",
                    "status": "error",
                    "detail": f"Agent run attempt {run.attempt} error: {run.error_code} - {run.error_message}",
                }
            )

    # 4. Audit logs related to this task
    stmt_audit = (
        select(AuditLog)
        .where(AuditLog.resource_type == "task")
        .where(AuditLog.resource_id == str(task_id))
        .where(AuditLog.organization_id == org_id)
        .order_by(AuditLog.created_at)
    )
    audit_result = await db.execute(stmt_audit)
    for audit in audit_result.scalars().all():
        events.append(
            {
                "time": audit.created_at,
                "stage": "audit",
                "status": audit.event,
                "detail": audit.payload.get("detail", audit.event)
                if audit.payload
                else audit.event,
            }
        )

    # Sort all events chronologically
    events.sort(key=lambda e: e["time"] or datetime.min.replace(tzinfo=None))

    return {"timeline": events}


@router.get("/agents")
async def list_agents() -> dict:
    container = get_container()
    return {"agents": [d.model_dump(mode="json") for d in container.registry.list_agents()]}


@router.get("/steps")
async def list_steps(
    request: Request,
    store: TaskStore = Depends(get_task_store),
    org_id=Depends(current_org),
    correlation_id: str | None = None,
    limit: int = 200,
) -> dict:
    """Audit trail: recent task steps, optionally filtered by correlation_id."""
    limit = max(1, min(limit, 500))
    steps = await store.list_steps(
        correlation_id=correlation_id, limit=limit, organization_id=org_id
    )
    return {"steps": steps}
