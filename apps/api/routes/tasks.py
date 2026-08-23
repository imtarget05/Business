"""Task + agent API routes.

Phase 0: POST /v1/tasks executes through the orchestrator skeleton with the
configured LLM provider (default: mock). Persistence of tasks/runs arrives in
Phase 1 — responses already carry the canonical contract shapes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from packages.contracts.models import TaskRequest
from packages.core.bootstrap import get_container
from packages.core.errors import ValidationError

router = APIRouter(prefix="/v1")


@router.post("/tasks")
async def create_task(request: TaskRequest, raw: Request) -> dict:
    container = get_container()
    if not request.payload and request.action != "ping":
        raise ValidationError("payload must not be empty", task_id=request.task_id)
    response = await container.orchestrator.execute(request)
    return response.model_dump(mode="json")


@router.get("/agents")
async def list_agents() -> dict:
    container = get_container()
    return {
        "agents": [d.model_dump(mode="json") for d in container.registry.list_agents()]
    }
