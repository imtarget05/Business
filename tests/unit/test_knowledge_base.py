"""Task 1 - KnowledgeBase unit tests (full-text + vector, sqlite mock)."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.core.knowledge_base import KnowledgeBase, chunk_text


class _BoWEmbeddingProvider:
    """Toy bag-of-words embedding for deterministic, semantic-like tests."""

    VOCAB = ["refund", "policy", "shipping", "warranty", "day", "return", "order"]

    @property
    def name(self) -> str:
        return "bow"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            toks = re.findall(r"[a-z]+", t.lower())
            out.append([float(toks.count(w)) for w in self.VOCAB])
        return out

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def kb():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = KnowledgeBase(factory)
    await base.init()
    yield base
    await engine.dispose()


@pytest.fixture()
async def kb_vec():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = KnowledgeBase(factory, embedding_provider=_BoWEmbeddingProvider())
    await base.init()
    yield base
    await engine.dispose()


def test_chunk_text_short_input_single_chunk() -> None:
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_text_respects_size_and_overlap() -> None:
    words = [f"w{i}" for i in range(1000)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 5
    for c in chunks:
        assert len(c.split()) <= 100
    assert chunks[0].split()[-20:] == chunks[1].split()[:20]


async def test_add_document_and_query_returns_relevant_chunk(kb) -> None:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "refund.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "Our refund policy allows refunds within 14 days of purchase. "
            "Shipping takes 3 to 5 business days for domestic orders."
        )
    stats = await kb.add_document(path)
    assert stats["chunks"] >= 1

    results = await kb.query("refund policy 14 days", k=3)
    assert results, "expected at least one relevant chunk"
    assert any("refund" in r.lower() for r in results)


async def test_query_no_match_returns_empty(kb) -> None:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "refund.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Refund policy: refunds within 14 days.")
    await kb.add_document(path)
    assert await kb.query("quantum banana xyzzy plugh", k=3) == []


async def test_query_respects_k_limit(kb) -> None:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "big.md")
    content = " ".join(
        f"alpha topic sentence number {i} about refunds and shipping policy." for i in range(300)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    await kb.add_document(path)

    results = await kb.query("refunds shipping policy", k=2)
    assert 1 <= len(results) <= 2


async def test_index_directory_counts_only_supported_files(kb) -> None:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.md"), "w", encoding="utf-8") as f:
        f.write("Document about refunds and warranty.")
    with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as f:
        f.write("Document about shipping and delivery.")
    with open(os.path.join(d, "ignore.log"), "w", encoding="utf-8") as f:
        f.write("not indexed")

    count = await kb.index_directory(d)
    assert count == 2
    assert await kb.query("warranty", k=5)


async def test_empty_question_returns_no_chunks(kb) -> None:
    assert await kb.query("", k=5) == []
    assert await kb.query("   ", k=5) == []


async def test_add_document_is_idempotent_on_reindex(kb) -> None:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "guide.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Alpha beta gamma delta epsilon zeta. " * 300)

    first = await kb.add_document(path)
    assert first["chunks"] >= 1

    second = await kb.add_document(path)

    source = str(Path(path).resolve())
    async with kb._factory() as session:
        row_count = (
            await session.execute(
                text("SELECT COUNT(*) FROM kb_chunks WHERE source_path = :sp"),
                {"sp": source},
            )
        ).scalar()

    assert row_count == first["chunks"] == second["chunks"], (
        f"re-index duplicated chunks: {row_count} rows for {source} "
        f"(expected {first['chunks']})"
    )


async def test_index_directory_twice_is_idempotent(kb) -> None:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.md"), "w", encoding="utf-8") as f:
        f.write("Document about refunds and warranty policy details.")
    with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as f:
        f.write("Document about shipping and delivery timelines.")

    first = await kb.index_directory(d)
    assert first == 2

    async with kb._factory() as session:
        after_first = (
            await session.execute(text("SELECT COUNT(*) FROM kb_chunks"))
        ).scalar()

    second = await kb.index_directory(d)
    assert second == 2

    async with kb._factory() as session:
        after_second = (
            await session.execute(text("SELECT COUNT(*) FROM kb_chunks"))
        ).scalar()

    assert after_second == after_first, "re-indexing the directory duplicated chunks"


# ---------------------------------------------------------------------------
# New: vector (semantic) retrieval
# ---------------------------------------------------------------------------


async def test_add_document_stores_embedding_column(kb_vec) -> None:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "refund.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Refund policy allows refunds within 14 days.")
    await kb_vec.add_document(path)
    async with kb_vec._factory() as session:
        emb = (
            await session.execute(text("SELECT embedding FROM kb_chunks LIMIT 1"))
        ).scalar()
    assert emb is not None
    assert emb.startswith("[")


async def test_query_vector_returns_relevant_chunk(kb_vec) -> None:
    d = tempfile.mkdtemp()
    p_refund = os.path.join(d, "refund.md")
    with open(p_refund, "w", encoding="utf-8") as f:
        f.write("Refund policy allows refunds within 14 days of purchase.")
    p_ship = os.path.join(d, "ship.md")
    with open(p_ship, "w", encoding="utf-8") as f:
        f.write("Shipping takes 3 to 5 business days for domestic orders.")
    await kb_vec.add_document(p_refund)
    await kb_vec.add_document(p_ship)

    results = await kb_vec.query_vector("refund policy", top_k=2)
    assert results, "expected at least one relevant chunk"
    assert any("refund" in r.lower() for r in results)
    assert results[0].lower().startswith("refund")


async def test_query_vector_empty_store_returns_empty(kb_vec) -> None:
    # No documents indexed yet -> nothing to compare against.
    assert await kb_vec.query_vector("any question at all", top_k=3) == []
