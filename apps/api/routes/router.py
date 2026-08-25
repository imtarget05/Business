"""Phase 4 — POST /v1/router/dispatch.

Free-form text in -> RouterAgent classifies -> orchestrator executes the
matched capability through the existing registry pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.bootstrap import get_container
from packages.core.router import RouterAgent
from packages.database.session import get_session
from packages.llm.factory import get_llm_provider

router = APIRouter(prefix="/v1/router", tags=["router"])


class DispatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    organization_id: str | None = None


@router.post("/dispatch")
async def dispatch(
    body: DispatchRequest, db: AsyncSession = Depends(get_session)
) -> dict:
    settings = get_settings()
    router_agent = RouterAgent(
        llm=get_llm_provider(settings),
        confidence_threshold=settings.router_confidence_threshold,
    )
    classification = await router_agent.classify_text(body.text)

    if classification.escalate or classification.capability is None:
        return {
            "status": "escalated",
            "reason": "no confident intent classification",
            "classification": {
                "domain": classification.domain,
                "action": classification.action,
                "confidence": classification.confidence,
                "source": classification.source,
            },
        }

    domain = Domain(classification.domain)
    request = TaskRequest(
        domain=domain,
        action=classification.action or "",
        payload={"text": body.text},
        context=TaskContext(organization_id=_org(body.organization_id)),
    )
    orchestrator = get_container().orchestrator
    response = await orchestrator.execute(request)
    return {
        "status": response.status.value,
        "agent": response.agent,
        "result": response.result,
        "citations": [c.model_dump(exclude_none=True) for c in response.citations],
        "error": response.error.model_dump() if response.error else None,
        "classification": {
            "domain": classification.domain,
            "action": classification.action,
            "confidence": classification.confidence,
            "source": classification.source,
        },
    }


def _org(raw: str | None):
    from uuid import UUID

    try:
        return UUID(raw) if raw else None
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="invalid organization_id") from exc
