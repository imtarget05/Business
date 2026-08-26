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

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import get_settings
from packages.contracts.enums import TaskStatus
from packages.contracts.models import TaskRequest
from apps.api.deps import current_org
from packages.core.bootstrap import get_container
from packages.core.errors import NotFoundError, ValidationError
from packages.core.persistence import NoopTaskStore, TaskStore
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


@router.get("/agents")
async def list_agents() -> dict:
    container = get_container()
    return {
        "agents": [d.model_dump(mode="json") for d in container.registry.list_agents()]
    }


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
