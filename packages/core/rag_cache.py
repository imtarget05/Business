"""Local RAG cache for verified Michelin answers (FTS + vector, PostgreSQL).

Why: AI-Engineer point 2 (RAG + Vector DB, no hallucination) + point 3 (cost -
serve repeat questions from local DB instead of re-calling web+LLM). Every
stored answer is paired with its source URLs so replies stay verifiable.

Feature 1 adds a hybrid retrieval path:
- Full-text search (tsvector, ``simple`` config - Vietnamese-safe) is tried
  first (exact hash hit, then fuzzy FTS).
- If FTS finds nothing, a vector-similarity fallback ranks cached answers by
  cosine similarity of their stored embedding against the query embedding.

Embeddings are stored as pgvector-literal strings (``[v0, v1, ...]``), which
work both in a native ``vector`` column (PostgreSQL + pgvector) and in a plain
TEXT column (SQLite fallback).

Falls back to no-op (returns None) on any DB error so the bot never hard-fails.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import uuid

from sqlalchemy import text

from packages.config.settings import get_settings
from packages.llm.embeddings import (
    _parse_vector,
    _vector_to_pg,
    cosine_similarity,
)


def _query_hash(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()


def _parse_urls(value: object) -> list[str]:
    """Return source URLs as a list (stored as a JSON string in the DB)."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(u) for u in value]
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return []
    return [str(u) for u in data] if isinstance(data, list) else []


def _engine():
    from sqlalchemy import create_engine

    _s = get_settings()
    _url = getattr(_s, "database_url", None) or getattr(_s, "DATABASE_URL", None)
    if not _url:
        return None
    if hasattr(_s, "database_url"):
        _url = _s.database_url
    elif hasattr(_s, "DATABASE_URL"):
        _url = _s.DATABASE_URL
    if not _url:
        return None
    return create_engine(_url, pool_pre_ping=True, future=True)


def _get_embedding_provider():
    """Resolve the configured embedding provider (injectable in tests)."""
    from packages.llm.factory import get_embedding_provider

    return get_embedding_provider(get_settings())


def _embed_sync(provider, texts: list[str]) -> list[list[float]]:
    """Run an async EmbeddingProvider.embed from synchronous RAG-cache code.

    Always runs the coroutine on a dedicated worker thread with its own event
    loop (``asyncio.run``). Reusing the running loop via
    ``run_coroutine_threadsafe`` deadlocks when called from the loop's own
    thread (e.g. inside an async FastAPI request), so a separate thread +
    fresh loop + timeout is used instead.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, provider.embed(texts)).result(timeout=30)


def _embed_query(question: str) -> list[float] | None:
    """Return the query embedding, or None if the provider is unavailable."""
    try:
        provider = _get_embedding_provider()
        vectors = _embed_sync(provider, [question])
        if vectors:
            return vectors[0]
    except Exception:
        return None
    return None


def rag_get(question: str, *, top_k: int = 1, similarity_threshold: float = 0.80) -> dict | None:
    """Return a cached verified answer, trying FTS first then vector similarity.

    Returns the best match or None.
    """
    try:
        eng = _engine()
        if eng is None:
            return None
        qh = _query_hash(question)
        with eng.connect() as conn:
            # Exact hash hit first (same question asked before).
            row = conn.execute(
                text(
                    "SELECT answer_text, source_urls FROM michelin_facts "
                    "WHERE query_hash = :qh LIMIT 1"
                ),
                {"qh": qh},
            ).fetchone()
            if row:
                return {"answer": row[0], "urls": _parse_urls(row[1])}
            # Fuzzy FTS fallback (same topic, different wording). On dialects
            # without tsvector (SQLite) this raises and we fall through to the
            # vector path below.
            try:
                row = conn.execute(
                    text(
                        "SELECT answer_text, source_urls FROM michelin_facts "
                        "WHERE tsv @@ plainto_tsquery('simple', :q) "
                        "ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC "
                        "LIMIT :k"
                    ),
                    {"q": question, "k": top_k},
                ).fetchone()
                if row:
                    return {"answer": row[0], "urls": _parse_urls(row[1])}
            except Exception:
                pass

            # --- Vector similarity fallback ---------------------------------
            qvec = _embed_query(question)
            if qvec is not None:
                best = None
                best_score = similarity_threshold
                if eng.dialect.name == "postgresql":
                    try:
                        row = conn.execute(
                            text(
                                "SELECT answer_text, source_urls, "
                                "embedding <=> CAST(:q AS vector) AS dist "
                                "FROM michelin_facts ORDER BY dist LIMIT :n"
                            ),
                            {"q": _vector_to_pg(qvec), "n": top_k},
                        ).fetchone()
                        if row is not None:
                            sim = 1.0 - float(row[2])
                            if sim > best_score:
                                best_score = sim
                                best = row
                    except Exception:
                        best = None
                if best is None:
                    rows = conn.execute(
                        text("SELECT answer_text, source_urls, embedding FROM michelin_facts")
                    ).fetchall()
                    for r in rows:
                        vec = _parse_vector(r[2])
                        if vec is None:
                            continue
                        score = cosine_similarity(qvec, vec)
                        if score > best_score:
                            best_score = score
                            best = r
                if best is not None:
                    return {"answer": best[0], "urls": _parse_urls(best[1])}
    except Exception:
        return None
    return None


def rag_store(question: str, answer: str, urls: list[str]) -> None:
    """Persist a verified answer + sources into the local RAG store.

    Also stores the query embedding (when an embedding provider is available)
    so vector-similarity retrieval can fall back to it later.
    """
    try:
        eng = _engine()
        if eng is None:
            return
        qh = _query_hash(question)
        emb_str: str | None = None
        qvec = _embed_query(question)
        if qvec is not None:
            emb_str = _vector_to_pg(qvec)
        with eng.begin() as conn:
            emb_sql = "CAST(:e AS vector)" if eng.dialect.name == "postgresql" else ":e"
            conn.execute(
                text(
                    "INSERT INTO michelin_facts (id, query_hash, question, "
                    "answer_text, source_urls, embedding) VALUES (:id, :qh, :q, :a, :u, "
                    + emb_sql
                    + ") ON CONFLICT (query_hash) DO UPDATE SET "
                    "answer_text = EXCLUDED.answer_text, "
                    "source_urls = EXCLUDED.source_urls, "
                    "embedding = EXCLUDED.embedding, "
                    "created_at = CURRENT_TIMESTAMP"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "qh": qh,
                    "q": question,
                    "a": answer,
                    "u": json.dumps(urls or [], ensure_ascii=False),
                    "e": emb_str,
                },
            )
    except Exception:
        pass


def rag_count() -> int:
    try:
        eng = _engine()
        if eng is None:
            return 0
        with eng.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM michelin_facts")).scalar() or 0)
    except Exception:
        return 0
