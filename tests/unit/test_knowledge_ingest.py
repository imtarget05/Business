"""Task 1 — knowledge ingestion helpers (full-text, no embedding).

Covers:
- chunk_text: deterministic chunking with overlap;
- index_file / index_directory: load + chunk + store into KnowledgeBase.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.knowledge.ingest import index_directory, index_file
from packages.core.knowledge_base import KnowledgeBase, chunk_text


@pytest.fixture()
async def kb(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'k.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = KnowledgeBase(factory)
    await base.init()
    yield base
    await engine.dispose()


def test_chunk_text_respects_max_tokens_and_overlap() -> None:
    words = [f"w{i}" for i in range(1000)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 5
    for c in chunks:
        assert len(c.split()) <= 100
    assert chunks[0].split()[-20:] == chunks[1].split()[:20]


def test_chunk_text_short_input_single_chunk() -> None:
    assert chunk_text("hello world", chunk_size=100, overlap=10) == ["hello world"]


async def test_index_file_stores_chunks(kb, tmp_path) -> None:
    doc = tmp_path / "refund.md"
    doc.write_text(
        "Our refund policy allows refunds within 14 days of purchase. "
        "Shipping takes 3 to 5 business days for domestic orders.",
        encoding="utf-8",
    )
    stats = await index_file(kb, doc)
    assert stats["chunks"] >= 1
    results = await kb.query("refund policy 14 days", k=3)
    assert results
    assert any("refund" in r.lower() for r in results)


async def test_index_directory_ignores_unsupported_files(kb, tmp_path) -> None:
    (tmp_path / "a.md").write_text("Document about refunds and warranty.", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Document about shipping and delivery.", encoding="utf-8")
    (tmp_path / "ignore.log").write_text("not indexed", encoding="utf-8")
    count = await index_directory(kb, tmp_path)
    assert count == 2
    assert await kb.query("warranty", k=5)
