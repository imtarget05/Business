import pathlib

content = '''\"\"\"Knowledge Base (Second Brain) routes (Task 1).

- POST /v1/knowledge/index - index the ``data/kb`` directory into the KB.
- POST /v1/knowledge/query - full-text \"ASK ANYTHING\" question -> cited answer.
- GET /v1/knowledge/documents - list all documents for the caller's org.
- POST /v1/knowledge/ingest - ingest a document (chunk, embed, store).
- DELETE /v1/knowledge/documents/{doc_id} - delete a document and its chunks.

Organization binding is SERVER-SIDE (from the caller's API key); the knowledge
base itself is a shared second brain, not per-tenant.
\"\"\"

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from apps.api.deps import current_org
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.errors import DatabaseError, ValidationError
from packages.database.session import get_session

router = APIRouter(prefix=\"/v1/knowledge\", tags=[\"knowledge\"])

# data/kb lives at the repo root; resolve relative to the current working dir.
KB_DIR = Path(\"data/kb\")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


class IngestRequest(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


@router.post(\"/index\")
async def index(
    request: Request,
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from packages.core.bootstrap import get_container

    container = get_container()
    kb = container.kb
    if kb is None:
        raise DatabaseError(\"knowledge base not configured\")
    try:
        count = await kb.index_directory(KB_DIR)
    except Exception as exc:  # noqa: BLE001 - surface as 500, no internals leak
        raise DatabaseError(\"knowledge indexing failed\") from exc
    return {\"indexed\": count, \"source\": str(KB_DIR)}


@router.post(\"/query\")
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
        raise DatabaseError(\"knowledge base not configured\")

    # Use the registered knowledge agent (full-text retrieval + LLM synthesis).
    descriptor, handler = container.registry.get_by_capability(\"knowledge.query\")
    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.KNOWLEDGE,
        action=\"query\",
        payload={\"question\": body.question},
        context=TaskContext(organization_id=_org_id(request), channel=\"dashboard\"),
    )
    response = await handler.handle(req)
    if response.status.value == \"rejected\":
        detail = response.error.message if response.error else \"rejected\"
        raise ValidationError(detail)
    return {
        \"answer\": response.result.get(\"answer\"),
        \"confidence\": response.confidence,
        \"citations\": [c.model_dump(exclude_none=True) for c in response.citations],
        \"refused_to_answer\": response.result.get(\"answer\") == NO_INFO_ANSWER,
    }


@router.get(\"/documents\")
async def list_documents(
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from packages.database.repositories.documents import KnowledgeRepository

    repo = KnowledgeRepository(db)
    documents = await repo.list_documents(org_id)
    result = []
    for doc in documents:
        chunks = await repo.list_chunks(doc.id)
        result.append({
            \"id\": str(doc.id),
            \"title\": doc.title,
            \"status\": doc.status.value,
            \"chunk_count\": len(chunks),
            \"source_type\": doc.source_type,
            \"created_at\": doc.created_at.isoformat() if doc.created_at else None,
        })
    return {\"documents\": result}


@router.post(\"/ingest\")
async def ingest(
    body: IngestRequest,
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from packages.config.settings import get_settings
    from packages.core.knowledge_base import chunk_text
    from packages.database.models import DocumentChunk, DocumentStatus
    from packages.database.repositories.documents import KnowledgeRepository, new_document
    from packages.llm.factory import get_embedding_provider

    repo = KnowledgeRepository(db)

    doc = new_document(
        organization_id=org_id,
        title=body.title,
        source_type=\"file\",
    )
    await repo.add_document(doc)

    pieces = chunk_text(body.content)

    embeddings: list[list[float]] = []
    if pieces:
        try:
            provider = get_embedding_provider(get_settings())
            embeddings = await provider.embed(pieces)
        except Exception:
            embeddings = []

    chunks: list[DocumentChunk] = []
    for idx, piece in enumerate(pieces):
        emb = embeddings[idx] if idx < len(embeddings) else None
        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=idx,
            content=piece,
            embedding=emb,
        )
        chunks.append(chunk)

    if chunks:
        await repo.add_chunks(chunks)

    doc.status = DocumentStatus.chunked
    doc.chunk_count = len(chunks)
    await db.commit()

    return {
        \"id\": str(doc.id),
        \"title\": doc.title,
        \"chunks_created\": len(chunks),
    }


@router.delete(\"/documents/{doc_id}\")
async def delete_document(
    doc_id: UUID,
    org_id: UUID = Depends(current_org),
    db=Depends(get_session),
) -> dict:
    from packages.database.repositories.documents import KnowledgeRepository

    repo = KnowledgeRepository(db)
    deleted = await repo.delete_document(org_id, doc_id)
    return {\"deleted\": deleted, \"id\": str(doc_id)}


def _org_id(request: Request) -> UUID | None:
    org = getattr(request.state, \"organization_id\", None)
    if org is None:
        return None
    if isinstance(org, UUID):
        return org
    try:
        return UUID(str(org))
    except (ValueError, TypeError):
        return None
'''

path = pathlib.Path(r'D:\Business Ops Agent Swarm\apps\api\routes\knowledge.py')
path.write_text(content, encoding='utf-8')
print('File written successfully')
