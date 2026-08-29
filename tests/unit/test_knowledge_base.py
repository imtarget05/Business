"""Task 1 — KnowledgeBase unit tests (full-text, no embedding, sqlite mock).

Covers chunking, ingestion, and lexical full-text retrieval on SQLite (the
portable path; PostgreSQL uses tsvector ranking via the same public API).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.core.knowledge_base import KnowledgeBase, chunk_text


@pytest.fixture()
async def kb():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = KnowledgeBase(factory)
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
    # Consecutive chunks overlap by `overlap` words.
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
