# -*- coding: utf-8 -*-
"""E2E: RAG cache warm path + vector fallback (offline, mock embedding).

Covers:
  - rag_store then rag_get returns the cached answer (cache hit) with no network.
  - When FTS misses, the vector-similarity fallback in rag_cache resolves the
    cached answer via the stored embedding.
  - The Knowledge Base query_vector path returns an embedded document even when
    full-text search finds nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text as _text

from packages.core.rag_cache import rag_get, rag_store
from packages.llm.mock_embedding import MockEmbeddingProvider


def _make_michelin_db(url: str):
    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        conn.execute(
            _text(
                "CREATE TABLE IF NOT EXISTS michelin_facts ("
                "id VARCHAR(36) PRIMARY KEY, "
                "query_hash VARCHAR(64) NOT NULL UNIQUE, "
                "question TEXT NOT NULL, "
                "answer_text TEXT NOT NULL, "
                "source_urls TEXT, "
                "embedding TEXT, "
                "tsv TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    return eng


@pytest.mark.e2e
def test_rag_cache_store_then_get_hit(tmp_path, monkeypatch):
    db = tmp_path / "rag.sqlite"
    url = f"sqlite:///{db}"
    _make_michelin_db(url)

    import packages.core.rag_cache as rc
    from packages.config.settings import Settings

    prov = MockEmbeddingProvider(dim=8)
    prov.script([1.0, 0, 0, 0, 0, 0, 0, 0], [1.0, 0, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(rc, "get_settings", lambda: Settings(database_url=url))
    monkeypatch.setattr(rc, "_get_embedding_provider", lambda: prov)

    # Warm the cache.
    rag_store(
        "Toi quen mat khau thi phai lam sao?",
        "Hay su dung lien ket dat lai mat khau tren trang dang nhap.",
        ["https://example.com/reset"],
    )

    # Exact-hash hit returns the cached answer verbatim.
    hit = rag_get("toi quen mat khau thi phai lam sao?")
    assert hit is not None
    assert "dat lai mat khau" in hit["answer"]
    assert hit["urls"] == ["https://example.com/reset"]


@pytest.mark.e2e
def test_rag_cache_vector_fallback_on_fts_miss(tmp_path, monkeypatch):
    db = tmp_path / "rag.sqlite"
    url = f"sqlite:///{db}"
    _make_michelin_db(url)

    import packages.core.rag_cache as rc
    from packages.config.settings import Settings

    prov = MockEmbeddingProvider(dim=8)
    prov.script([1.0, 0, 0, 0, 0, 0, 0, 0], [1.0, 0, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(rc, "get_settings", lambda: Settings(database_url=url))
    monkeypatch.setattr(rc, "_get_embedding_provider", lambda: prov)

    rag_store(
        "Cach dat lai mat khau tai khoan",
        "Mo trang dang nhap va chon Quen mat khau.",
        ["https://example.com/help"],
    )

    # A query with zero token overlap with the stored question should still be
    # resolved by the vector-similarity fallback.
    vec = rag_get("zzz qqq totally unrelated tokens xyzzy")
    assert vec is not None
    assert "Quen mat khau" in vec["answer"] or "dang nhap" in vec["answer"]


@pytest.mark.e2e
async def test_kb_query_vector_returns_stored_doc_when_fts_misses(sqlite_kb):
    """Full-text misses on unrelated tokens, but the vector path still returns the
    embedded document."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        doc_path = Path(d) / "vpn.md"
        doc_path.write_text(
            "Huong dan dat lai mat khau VPN va cau hinh truy cap tu xa.",
            encoding="utf-8",
        )
        await sqlite_kb.add_document(doc_path)

        # Full-text search on disjoint tokens -> empty.
        fts = await sqlite_kb.query("zzz qqq unrelated tokens", k=3)
        assert fts == []

        # Vector retrieval still surfaces the single embedded document.
        vec = await sqlite_kb.query_vector("zzz qqq unrelated tokens", top_k=3)
        assert vec, "vector fallback should return the embedded document"
        assert "mat khau" in vec[0].lower()
