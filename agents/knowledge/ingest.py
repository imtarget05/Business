"""Knowledge ingestion pipeline (Phase 2 Task 2.2).

chunk -> embed -> persist documents + document_chunks with vectors.
Re-ingesting an existing title replaces the previous document atomically
(idempotent per (organization_id, title)).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agents.knowledge.agent import create_knowledge_agent  # noqa: F401  (re-export convenience)
from packages.database.models import Document, DocumentChunk, DocumentStatus
from packages.database.repositories.documents import (
    KnowledgeRepository,
    new_document,
)
from packages.llm.base import EmbeddingProvider


def chunk_text(text: str, max_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Whitespace-token chunking with overlap. Deterministic."""
    words = text.split()
    if not words:
        return []
    if len(words) <= max_tokens:
        return [text]
    step = max_tokens - overlap
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + max_tokens]
        if len(window) <= overlap and chunks:
            break
        chunks.append(" ".join(window))
        if start + max_tokens >= len(words):
            break
    return chunks


class IngestionService:
    def __init__(self, repo: KnowledgeRepository, embeddings: EmbeddingProvider) -> None:
        self._repo = repo
        self._embeddings = embeddings

    async def ingest(
        self,
        *,
        organization_id: UUID,
        title: str,
        content: str,
        source_type: str = "text",
        source_ref: str | None = None,
    ) -> Document:
        """Ingest one document. Idempotent per (organization_id, title)."""
        existing = await self._repo.find_document_by_title(organization_id, title)
        if existing is not None:
            # Replace in place: keep the document identity, drop old chunks.
            await self._repo.delete_chunks(existing.id)
            doc = existing
            doc.status = DocumentStatus.pending
            doc.chunk_count = 0
        else:
            doc = new_document(organization_id, title, source_type, source_ref)
            await self._repo.add_document(doc)

        pieces = chunk_text(content)
        vectors = await self._embeddings.embed(pieces)
        chunks = [
            DocumentChunk(
                document_id=doc.id,
                chunk_index=i,
                content=piece,
                token_count=len(piece.split()),
                embedding=vector,
            )
            for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ]
        await self._repo.add_chunks(chunks)

        doc.status = DocumentStatus.embedded
        doc.chunk_count = len(chunks)
        await self._session_flush()
        return doc

    async def _session_flush(self) -> None:
        await self._repo._session.flush()  # noqa: SLF001 — same transaction boundary


async def ingest_text(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    *,
    organization_id: UUID,
    title: str,
    content: str,
    source_type: str = "text",
) -> Document:
    """One-shot helper used by routes/scripts."""
    service = IngestionService(KnowledgeRepository(session), embeddings)
    return await service.ingest(
        organization_id=organization_id,
        title=title,
        content=content,
        source_type=source_type,
    )


__all__ = ["IngestionService", "chunk_text", "ingest_text"]
