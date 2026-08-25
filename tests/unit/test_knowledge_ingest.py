"""Phase 2 — knowledge ingestion pipeline + retrieval (TDD).

Covers:
- chunk_text: deterministic chunking with overlap;
- ingestion: document + chunks + embeddings persisted, idempotent re-ingest;
- knowledge.delete capability: removing a document cascades its chunks;
- retrieval: top-k semantic search with a hard similarity threshold — queries
  below the threshold return no results instead of weak context.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.knowledge.ingest import IngestionService, chunk_text
from packages.database import models
from packages.database.base import Base
from packages.database.repositories.documents import KnowledgeRepository
from packages.llm.mock_embedding import MockEmbeddingProvider


@pytest.fixture()
async def db():
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db()}")
    # pgvector Vector column renders fine on sqlite (type affinity), but the
    # repository must tolerate it — see repo design.
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                models.Organization.__table__,
                models.Document.__table__,
                models.DocumentChunk.__table__,
            ],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def tmp_db() -> str:
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path.replace("\\", "/")


@pytest.fixture()
def org_id(db):
    import uuid
    return uuid.uuid4()


DOC_LONG = " ".join(f"Sentence number {i} about refunds and shipping policy." for i in range(200))


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_respects_max_tokens_and_overlap() -> None:
    words = [f"w{i}" for i in range(1000)]
    text = " ".join(words)
    chunks = chunk_text(text, max_tokens=100, overlap=20)
    assert len(chunks) > 5
    # Every chunk within size budget
    for c in chunks:
        assert len(c.split()) <= 100
    # Overlap: consecutive chunks share words
    assert chunks[0].split()[-20:] == chunks[1].split()[:20]


def test_chunk_text_short_input_single_chunk() -> None:
    assert chunk_text("hello world", max_tokens=100, overlap=10) == ["hello world"]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def test_ingest_creates_document_chunks_and_embeddings(db, org_id) -> None:
    repo = KnowledgeRepository(db)
    service = IngestionService(repo, MockEmbeddingProvider())
    doc = await service.ingest(
        organization_id=org_id,
        title="Refund policy",
        content=DOC_LONG,
        source_type="text",
    )
    assert doc.status.value == "embedded"
    assert doc.chunk_count >= 2

    chunks = await repo.list_chunks(doc.id)
    assert len(chunks) == doc.chunk_count
    for c in chunks:
        assert c.embedding is not None
        assert len(list(c.embedding)) == 768


async def test_ingest_is_idempotent_per_title(db, org_id) -> None:
    repo = KnowledgeRepository(db)
    service = IngestionService(repo, MockEmbeddingProvider())
    d1 = await service.ingest(organization_id=org_id, title="FAQ", content=DOC_LONG)
    d2 = await service.ingest(organization_id=org_id, title="FAQ", content=DOC_LONG)
    assert d1.id == d2.id  # replaced, not duplicated
    docs = await repo.list_documents(org_id)
    assert len(docs) == 1


# ---------------------------------------------------------------------------
# knowledge.delete
# ---------------------------------------------------------------------------


async def test_delete_document_cascades_chunks(db, org_id) -> None:
    repo = KnowledgeRepository(db)
    service = IngestionService(repo, MockEmbeddingProvider())
    doc = await service.ingest(organization_id=org_id, title="Temp", content=DOC_LONG)

    deleted = await repo.delete_document(org_id, doc.id)
    assert deleted is True

    assert await repo.get_document(org_id, doc.id) is None
    assert await repo.list_chunks(doc.id) == []
    # Idempotent delete
    assert await repo.delete_document(org_id, doc.id) is False


async def test_delete_scoped_to_organization(db, org_id) -> None:
    import uuid
    repo = KnowledgeRepository(db)
    service = IngestionService(repo, MockEmbeddingProvider())
    doc = await service.ingest(organization_id=org_id, title="X", content="hello world content")
    other_org = uuid.uuid4()
    assert await repo.delete_document(other_org, doc.id) is False
    assert await repo.get_document(org_id, doc.id) is not None


# ---------------------------------------------------------------------------
# Retrieval with hard similarity threshold
# ---------------------------------------------------------------------------


async def test_retrieval_returns_top_k_above_threshold(db, org_id) -> None:
    class TopicEmbedding(MockEmbeddingProvider):
        """Embeds by topic keyword presence — gives real semantic direction."""

        TOPICS = {
            "refund": [1.0, 0.0, 0.0],
            "shipping": [0.0, 1.0, 0.0],
            "warranty": [0.0, 0.0, 1.0],
        }

        async def embed(self, texts):
            out = []
            for t in texts:
                v = [0.05, 0.05, 0.05]
                for topic, vec in self.TOPICS.items():
                    if topic in t.lower():
                        v = [a + b for a, b in zip(v, vec, strict=False)]
                norm = sum(x * x for x in v) ** 0.5
                v = [x / norm for x in v]
                out.append(v + [0.0] * (768 - len(v)))  # pad to model dim
            return out

    repo = KnowledgeRepository(db)
    provider = TopicEmbedding()
    service = IngestionService(repo, provider)
    await service.ingest(organization_id=org_id, title="Shipping", content=DOC_LONG)

    hits = await repo.search(
        organization_id=org_id,
        query="refunds and shipping policy",
        top_k=3,
        query_embedding=(await provider.embed(["refunds and shipping policy"]))[0],
    )
    assert len(hits) >= 1
    chunk, score = hits[0]
    assert score > 0.75
    assert "refunds" in chunk.content.lower()


async def test_retrieval_below_threshold_returns_empty(db, org_id) -> None:
    """HARD ACCEPTANCE CRITERION: weak/no context => no results, never guess."""
    repo = KnowledgeRepository(db)
    provider = MockEmbeddingProvider()
    service = IngestionService(repo, provider)
    await service.ingest(organization_id=org_id, title="Shipping", content=DOC_LONG)

    # Completely unrelated gibberish query hashes far from the doc chunks.
    hits = await repo.search(
        organization_id=org_id,
        query="zzz qqq xyzzy plugh quantum banana unicorn",
        top_k=3,
        threshold=0.99,
    )
    assert hits == []
