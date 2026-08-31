# -*- coding: utf-8 -*-
"""Local RAG cache for verified Michelin answers (Vector/FTS via PostgreSQL).

Why: AI-Engineer point 2 (RAG + Vector DB, no hallucination) + point 3 (cost —
serve repeat questions from local DB instead of re-calling web+LLM). Every
stored answer is paired with its source URLs so replies stay verifiable.

Uses PostgreSQL full-text search with the `simple` config (Vietnamese-safe).
Falls back to no-op (returns None) on any DB error so the bot never hard-fails.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Optional

from sqlalchemy import text

from packages.config.settings import get_settings


def _query_hash(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()


def _engine():
    from sqlalchemy import create_engine

    _s = get_settings()
    _url = getattr(_s, "database_url", None) or getattr(_s, "DATABASE_URL", None)
    if not _url:
        return None
    # Accept either attribute name used across the codebase.
    if hasattr(_s, "database_url"):
        _url = _s.database_url
    elif hasattr(_s, "DATABASE_URL"):
        _url = _s.DATABASE_URL
    if not _url:
        return None
    return create_engine(_url, pool_pre_ping=True, future=True)


def rag_get(question: str, *, top_k: int = 1) -> Optional[dict]:
    """Return a cached verified answer if the question matches local FTS.

    Matching is fuzzy: we look for rows whose tsvector contains any significant
    token of the question. Returns the best match or None.
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
                return {"answer": row[0], "urls": row[1] or []}
            # Fuzzy FTS fallback (same topic, different wording).
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
                return {"answer": row[0], "urls": row[1] or []}
    except Exception:
        return None
    return None


def rag_store(question: str, answer: str, urls: list[str]) -> None:
    """Persist a verified answer + sources into the local RAG store."""
    try:
        eng = _engine()
        if eng is None:
            return
        qh = _query_hash(question)
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO michelin_facts (id, query_hash, question, "
                    "answer_text, source_urls) VALUES (:id, :qh, :q, :a, :u) "
                    "ON CONFLICT (query_hash) DO UPDATE SET "
                    "answer_text = EXCLUDED.answer_text, "
                    "source_urls = EXCLUDED.source_urls, "
                    "created_at = now()"
                ),
                {
                    "id": uuid.uuid4(),
                    "qh": qh,
                    "q": question,
                    "a": answer,
                    "u": json.dumps(urls or [], ensure_ascii=False),
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
