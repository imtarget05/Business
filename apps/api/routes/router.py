"""Phase 4 — POST /v1/router/dispatch.

Free-form text in -> RouterAgent classifies -> orchestrator executes the
matched capability through the existing registry pipeline.

Like POST /v1/tasks, dispatched tasks go through the task store + recorder so
they get durable ids and audit rows when persistence is enabled. The caller's
organization is bound SERVER-SIDE from their API key.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org
from apps.api.routes.tasks import get_task_store
from packages.config.settings import get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.bootstrap import get_container
from packages.core.persistence import TaskStore
from packages.core.response_presentation import present
from packages.core.router import RouterAgent
from packages.database.session import get_session
from packages.llm.factory import get_llm_provider

router = APIRouter(prefix="/v1/router", tags=["router"])


class DispatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID | None = None
    parent_message_id: UUID | None = None


@router.post("/dispatch")
async def dispatch(
    body: DispatchRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
    store: TaskStore = Depends(get_task_store),
) -> dict:
    settings = get_settings()
    container = get_container()
    router_agent = RouterAgent(
        llm=get_llm_provider(settings),
        registry=container.registry,
        confidence_threshold=settings.router_confidence_threshold,
    )
    classification = await router_agent.classify_text(body.text)

    if classification.escalate or classification.capability is None:
        # Friendly escalation: suggest the top candidate agents instead of a
        # bare refusal, so the user can pick one quickly (inline buttons on
        # Telegram consume this field).
        from packages.core.router import score_candidates

        candidates = score_candidates(body.text, container.registry)[:3]
        return {
            "status": "escalated",
            "reason": "no confident intent classification",
            "suggested_intents": [
                {
                    "agent": agent,
                    "score": score,
                    "hint": (
                        f"Thử yêu cầu liên quan tới {agent.rsplit('-v', 1)[0].replace('_', ' ')}"
                    ),
                }
                for agent, score in candidates
            ],
            "classification": {
                "domain": classification.domain,
                "action": classification.action,
                "confidence": classification.confidence,
                "source": classification.source,
            },
        }

    domain = Domain(classification.domain)
    req = TaskRequest(
        domain=domain,
        action=classification.action or "",
        payload={"text": body.text},
        context=TaskContext(
            organization_id=org_id,
            conversation_id=body.conversation_id,
            parent_message_id=body.parent_message_id,
            channel="api",
        ),
    )

    resolution = await store.resolve(req)
    if resolution.response is not None:
        # Idempotent replay of an already-terminal task.
        response = resolution.response
    else:
        recorder = store  # SqlAlchemyTaskStore also implements TaskRecorder
        orchestrator = get_container().orchestrator
        try:
            response = await orchestrator.execute(
                req, recorder=recorder, policy=get_container().policy
            )
        except Exception:
            await store.rollback()
            raise
        await store.complete(req.task_id, response)

    return {
        "task_id": str(req.task_id),
        "status": response.status.value,
        "agent": response.agent,
        "result": response.result,
        "citations": [c.model_dump(exclude_none=True) for c in response.citations],
        "error": response.error.model_dump() if response.error else None,
        "friendly": present(response),
        "classification": {
            "domain": classification.domain,
            "action": classification.action,
            "confidence": classification.confidence,
            "source": classification.source,
        },
    }
