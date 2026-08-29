"""Knowledge Base (Second Brain) routes (Task 1).

- POST /v1/knowledge/index — index the ``data/kb`` directory into the KB.
- POST /v1/knowledge/query — full-text "ASK ANYTHING" question -> cited answer.

Organization binding is SERVER-SIDE (from the caller's API key); the knowledge
base itself is a shared second brain, not per-tenant.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from packages.config.settings import get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.errors import DatabaseError, ValidationError
from packages.database.session import get_session
from apps.api.deps import current_org

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

# data/kb lives at the repo root; resolve relative to the current working dir.
KB_DIR = Path("data/kb")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("/index")
async def index(
    request: Request,
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from packages.core.bootstrap import get_container

    container = get_container()
    kb = container.kb
    if kb is None:
        raise DatabaseError("knowledge base not configured")
    try:
        count = await kb.index_directory(KB_DIR)
    except Exception as exc:  # noqa: BLE001 - surface as 500, no internals leak
        raise DatabaseError("knowledge indexing failed") from exc
    return {"indexed": count, "source": str(KB_DIR)}


@router.post("/query")
async def query(
    body: QueryRequest,
    request: Request,
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from agents.knowledge.agent import NO_INFO_ANSWER
    from packages.core.bootstrap import get_container

    container = get_container()
    kb = container.kb
    if kb is None:
        raise DatabaseError("knowledge base not configured")

    # Use the registered knowledge agent (full-text retrieval + LLM synthesis).
    descriptor, handler = container.registry.get_by_capability("knowledge.query")
    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": body.question},
        context=TaskContext(organization_id=_org_id(request), channel="dashboard"),
    )
    response = await handler.handle(req)
    if response.status.value == "rejected":
        detail = response.error.message if response.error else "rejected"
        raise ValidationError(detail)
    return {
        "answer": response.result.get("answer"),
        "confidence": response.confidence,
        "citations": [c.model_dump(exclude_none=True) for c in response.citations],
        "refused_to_answer": response.result.get("answer") == NO_INFO_ANSWER,
    }


def _org_id(request: Request) -> UUID | None:
    org = getattr(request.state, "organization_id", None)
    if org is None:
        return None
    if isinstance(org, UUID):
        return org
    try:
        return UUID(str(org))
    except (ValueError, TypeError):
        return None
