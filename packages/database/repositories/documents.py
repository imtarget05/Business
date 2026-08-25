"""Knowledge repository — documents, chunks, vector search, deletion.

Phase 2. Storage: PostgreSQL + pgvector in production; sqlite (tests) stores
embeddings as JSON-compatible floats via the Vector column type's plain list
affinity. Similarity is cosine distance computed in Python when the backend
lacks pgvector operators, and via `<=>` on PostgreSQL.

The hard similarity threshold contract lives here: `search()` never returns
hits below `threshold`.
"""

from __future__ import annotations

import math
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Document, DocumentChunk, DocumentStatus


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def get_document(
        self, organization_id: UUID, document_id: UUID
    ) -> Document | None:
        doc = await self._session.get(Document, document_id)
        if doc is None or doc.organization_id != organization_id:
            return None
        return doc

    async def find_document_by_title(
        self, organization_id: UUID, title: str
    ) -> Document | None:
        stmt = select(Document).where(
            Document.organization_id == organization_id,
            Document.title == title,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def list_documents(self, organization_id: UUID) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.organization_id == organization_id)
            .order_by(Document.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_document(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        return document

    async def delete_document(self, organization_id: UUID, document_id: UUID) -> bool:
        """Remove a document and all of its chunks. Idempotent.

        Scoped to the organization — a foreign org id can never delete a doc.
        """
        doc = await self.get_document(organization_id, document_id)
        if doc is None:
            return False
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        await self._session.delete(doc)
        await self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    async def add_chunks(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        self._session.add_all(chunks)
        await self._session.flush()
        return chunks

    async def delete_chunks(self, document_id: UUID) -> None:
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        await self._session.flush()

    async def list_chunks(self, document_id: UUID) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def all_chunks_for_org(self, organization_id: UUID) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(Document.organization_id == organization_id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(
        self,
        organization_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        threshold: float = 0.75,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """Top-k semantic search above a HARD similarity threshold.

        Returns [(chunk, score)] sorted by score desc. Scores below
        `threshold` are dropped — callers must never receive weak context.
        """
        from packages.config.settings import get_settings
        from packages.llm.factory import get_embedding_provider

        if query_embedding is None:
            provider = get_embedding_provider(get_settings())
            query_embedding = (await provider.embed([query]))[0]

        scored: list[tuple[DocumentChunk, float]] = []
        for chunk in await self.all_chunks_for_org(organization_id):
            if chunk.embedding is None:
                continue
            emb = list(chunk.embedding)
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold:
                scored.append((chunk, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


def new_document(
    organization_id: UUID,
    title: str,
    source_type: str,
    source_ref: str | None = None,
) -> Document:
    return Document(
        organization_id=organization_id,
        title=title,
        source_type=source_type,
        source_ref=source_ref,
        status=DocumentStatus.pending,
    )


__all__ = ["KnowledgeRepository", "new_document", "_cosine_similarity"]
