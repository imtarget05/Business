"""Knowledge management routes (Phase 2 Task 2.5).

- POST /v1/knowledge/ingest  — chunk+embed+store a document
- POST /v1/knowledge/query   — RAG answer with citations (threshold-gated)
- DELETE /v1/knowledge/documents/{id} — remove doc + chunks (org-scoped)

Organization binding is SERVER-SIDE: the caller's organization comes from
their API key (`current_org`), never from the request body.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.knowledge.agent import NO_INFO_ANSWER, KnowledgeAgent
from agents.knowledge.ingest import IngestionService
from apps.api.deps import current_org
from packages.config.settings import get_settings
from packages.core.errors import DatabaseError, NotFoundError, ValidationError
from packages.database.repositories.documents import KnowledgeRepository
from packages.database.session import get_session
from packages.llm.factory import get_embedding_provider, get_llm_provider
from packages.observability.logging import get_logger

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
logger = get_logger("knowledge")


class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    source_type: str = "text"
    source_ref: str | None = None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("/ingest")
async def ingest(
    body: IngestRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> dict:
    service = IngestionService(KnowledgeRepository(db), get_embedding_provider(get_settings()))
    try:
        doc = await service.ingest(
            organization_id=org_id,
            title=body.title,
            content=body.content,
            source_type=body.source_type,
            source_ref=body.source_ref,
        )
    except Exception as exc:
        await db.rollback()
        logger.error("knowledge_ingest_failed", extra={"type": type(exc).__name__})
        raise DatabaseError("ingest failed") from exc
    await db.commit()
    logger.info("knowledge_ingested", extra={"document_id": str(doc.id), "chunks": doc.chunk_count})
    return {
        "document_id": str(doc.id),
        "title": doc.title,
        "status": doc.status.value,
        "chunk_count": doc.chunk_count,
    }


@router.post("/query")
async def query(
    body: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> dict:
    s = get_settings()
    agent = KnowledgeAgent(
        repository=KnowledgeRepository(db),
        llm=get_llm_provider(s),
        embeddings=get_embedding_provider(s),
        similarity_threshold=s.knowledge_similarity_threshold,
    )
    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    req = TaskRequest(
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": body.question},
        context=TaskContext(organization_id=org_id, channel="dashboard"),
        task_id=uuid.uuid4(),
    )
    response = await agent.handle(req)
    if response.status.value == "rejected":
        detail = response.error.message if response.error else "rejected"
        raise ValidationError(detail)
    return {
        "answer": response.result.get("answer"),
        "confidence": response.confidence,
        "citations": [c.model_dump(exclude_none=True) for c in response.citations],
        "refused_to_answer": response.result.get("answer") == NO_INFO_ANSWER,
    }


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> dict:
    repo = KnowledgeRepository(db)
    deleted = await repo.delete_document(org_id, document_id)
    await db.commit()
    if not deleted:
        raise NotFoundError("document not found")
    return {"deleted": True, "document_id": str(document_id)}


@router.get("/documents")
async def list_documents(
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> dict:
    repo = KnowledgeRepository(db)
    docs = await repo.list_documents(org_id)
    return {
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "status": d.status.value,
                "chunk_count": d.chunk_count,
                "source_type": d.source_type,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    }
