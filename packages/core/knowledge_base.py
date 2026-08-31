"""Full-text + semantic Knowledge Base (Second Brain).

Task 1 deliverable: a centralized knowledge store (SOPs, docs, personal
files) answering natural-language questions. Feature 1 adds semantic (vector)
retrieval alongside the original full-text search, using a hybrid approach.

Design decisions:
- Full-text uses a ``tsvector`` GIN index (PostgreSQL) or in-Python
  token-overlap scoring (SQLite / tests).
- Semantic search stores a vector embedding per chunk (via EmbeddingProvider)
  and ranks by cosine similarity. On PostgreSQL it uses the native pgvector
  ``<=>`` operator when available; on SQLite (and as a safety net everywhere)
  it falls back to in-Python cosine over the stored vector.
- The FTS path is fully preserved — this is additive, not a replacement.

The table is created by migration ``0009_kb_chunks`` in production (extended by
``0011_add_vector_embedding``); tests and local dev create it on first use via
:meth:`init` / lazy ``_ensure_schema``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.llm.embeddings import _parse_vector, _vector_to_pg, cosine_similarity

# ---------------------------------------------------------------------------
# Chunking + tokenization
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 500  # words per chunk
DEFAULT_CHUNK_OVERLAP = 50  # words of overlap between consecutive chunks

# Token pattern: latin/digit words plus accented Vietnamese letters, >= 2 chars.
_TOKEN_RE = re.compile(r"[a-zA-Z0-9à-ỹÀ-Ỹ_]+")


def chunk_text(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
    """Split ``text`` into word-overlapping chunks (~``chunk_size`` words).

    Deterministic. Short inputs return a single chunk. Overlap lets a sentence
    split across a boundary remain partially present in both chunks.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [" ".join(words)]
    step = max(chunk_size - overlap, 1)
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        # Don't emit a tail that is entirely overlap with the previous chunk.
        if chunks and len(window) <= overlap:
            break
        chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


def _score_query(tokens: list[str], content: str) -> int:
    """Lexical relevance score: summed term frequency of query tokens."""
    if not tokens:
        return 0
    ct = _tokenize(content)
    if not ct:
        return 0
    ct_set = set(ct)
    distinct = sum(1 for t in tokens if t in ct_set)
    if distinct == 0:
        return 0
    freq = sum(ct.count(t) for t in tokens)
    return freq


def _default_embedding_provider():
    """Lazily resolve the configured embedding provider (avoids import cycles)."""
    from packages.config.settings import get_settings
    from packages.llm.factory import get_embedding_provider

    return get_embedding_provider(get_settings())


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------


class KnowledgeBase:
    """Full-text + semantic document store and retriever.

    Parameters
    ----------
    session_factory:
        An ``async_sessionmaker`` bound to the target engine (PostgreSQL in
        production, SQLite in tests / local dev).
    chunk_size / overlap:
        Chunking parameters (words).
    embedding_provider:
        Optional EmbeddingProvider. When omitted, the configured provider is
        resolved lazily (MockEmbeddingProvider for ``mock`` settings).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[Any],
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
        embedding_provider: Any = None,
    ) -> None:
        self._factory = session_factory
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._embedding_provider = embedding_provider
        self._schema_ready = False
        self._lock: asyncio.Lock | None = None

    # -- schema -------------------------------------------------------------

    async def init(self) -> None:
        """Create the ``kb_chunks`` table if it does not exist yet."""
        await self._ensure_schema()

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._schema_ready:
                return
            async with self._factory() as session:
                dialect = session.bind.dialect.name
                if dialect == "postgresql":
                    await session.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS kb_chunks (
                                id UUID PRIMARY KEY,
                                doc_id UUID NOT NULL,
                                source_path TEXT NOT NULL,
                                title TEXT NOT NULL,
                                chunk_index INTEGER NOT NULL,
                                content TEXT NOT NULL,
                                embedding vector(768),
                                search_vector tsvector
                                    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                                created_at TIMESTAMP NOT NULL DEFAULT now()
                            )
                            """
                        )
                    )
                    await session.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_kb_chunks_tsvector "
                            "ON kb_chunks USING gin (search_vector)"
                        )
                    )
                else:
                    await session.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS kb_chunks (
                                id VARCHAR(36) PRIMARY KEY,
                                doc_id VARCHAR(36) NOT NULL,
                                source_path TEXT NOT NULL,
                                title TEXT NOT NULL,
                                chunk_index INTEGER NOT NULL,
                                content TEXT NOT NULL,
                                embedding TEXT,
                                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                            )
                            """
                        )
                    )
                # Migration safety: ensure the embedding column exists even when
                # the table was created by an older migration lacking it.
                try:
                    if dialect == "postgresql":
                        await session.execute(
                            text(
                                "ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS "
                                "embedding vector(768)"
                            )
                        )
                    else:
                        await session.execute(
                            text("ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS embedding TEXT")
                        )
                except Exception:
                    pass
                await session.commit()
            self._schema_ready = True

    # -- ingestion ----------------------------------------------------------

    async def add_document(self, path: str | Path) -> dict[str, Any]:
        """Load a ``.md`` / ``.txt`` / ``.pdf`` file, chunk it, and store chunks.

        Each chunk is embedded (via the embedding provider) and the vector is
        stored alongside the content and tsvector.

        Returns a stats dict: ``{doc_id, title, source_path, chunks}``.
        """
        p = Path(path)
        content = self._read_file(p)
        title = p.stem
        doc_id = uuid.uuid4()
        pieces = chunk_text(content, self._chunk_size, self._overlap)

        # Embed every chunk in one batch call (embedding provider is async).
        embeddings: list[list[float]] = []
        try:
            provider = self._embedding_provider or _default_embedding_provider()
            embeddings = await provider.embed(pieces)
        except Exception:
            embeddings = []

        await self._ensure_schema()
        async with self._factory() as session:
            # Idempotent re-index: drop any chunks previously stored for this
            # exact source *before* (re)inserting.
            await session.execute(
                text("DELETE FROM kb_chunks WHERE source_path = :sp"),
                {"sp": str(p.resolve())},
            )
            for idx, piece in enumerate(pieces):
                emb = embeddings[idx] if idx < len(embeddings) else None
                emb_sql = (
                    "CAST(:embedding AS vector)"
                    if session.bind.dialect.name == "postgresql"
                    else ":embedding"
                )
                await session.execute(
                    text(
                        "INSERT INTO kb_chunks "
                        "(id, doc_id, source_path, title, chunk_index, content, embedding, created_at) "
                        "VALUES (:id, :doc_id, :source_path, :title, :chunk_index, :content, "
                        + emb_sql
                        + ", CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "doc_id": str(doc_id),
                        "source_path": str(p.resolve()),
                        "title": title,
                        "chunk_index": idx,
                        "content": piece,
                        "embedding": _vector_to_pg(emb) if emb else None,
                    },
                )
            await session.commit()

        return {
            "doc_id": str(doc_id),
            "title": title,
            "source_path": str(p.resolve()),
            "chunks": len(pieces),
        }

    async def index_directory(self, path: str | Path) -> int:
        """Index every ``.md`` / ``.txt`` / ``.pdf`` file under ``path``."""
        base = Path(path)
        if not base.exists():
            return 0
        count = 0
        for file in sorted(base.iterdir()):
            if file.is_file() and file.suffix.lower() in {".md", ".txt", ".pdf"}:
                await self.add_document(file)
                count += 1
        return count

    # -- retrieval ----------------------------------------------------------

    async def query(self, query_text: str, k: int = 5) -> list[str]:
        """Return the ``k`` most relevant chunk contents for ``text`` (full-text).

        PostgreSQL uses tsvector full-text ranking; SQLite uses in-Python
        token-overlap scoring. Returns ``[]`` when nothing matches.
        """
        await self._ensure_schema()
        q_tokens = _tokenize(query_text)
        if not q_tokens:
            return []

        async with self._factory() as session:
            dialect = session.bind.dialect.name
            if dialect == "postgresql":
                try:
                    rows = (
                        await session.execute(
                            text(
                                "SELECT content, ts_rank(search_vector, "
                                "plainto_tsquery('english', :q)) AS rank "
                                "FROM kb_chunks "
                                "WHERE search_vector @@ plainto_tsquery('english', :q) "
                                "ORDER BY rank DESC LIMIT :k"
                            ),
                            {"q": query_text, "k": k},
                        )
                    ).all()
                    if rows:
                        return [r[0] for r in rows]
                except Exception:
                    pass
            contents = (
                (await session.execute(text("SELECT content FROM kb_chunks"))).scalars().all()
            )

        scored = [(_score_query(q_tokens, c), c) for c in contents]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]

    async def query_vector(self, query_text: str, top_k: int = 5) -> list[str]:
        """Return the ``top_k`` most semantically similar chunk contents.

        On PostgreSQL it uses the native pgvector ``<=>`` (cosine distance)
        operator when an embedding column is present; otherwise (and as a
        safety net on every dialect) it computes cosine similarity in Python
        over the stored vector. Returns ``[]`` when nothing is embedded or
        embedding fails.
        """
        await self._ensure_schema()
        try:
            provider = self._embedding_provider or _default_embedding_provider()
            qvec = (await provider.embed([query_text]))[0]
        except Exception:
            return []

        async with self._factory() as session:
            dialect = session.bind.dialect.name
            if dialect == "postgresql":
                try:
                    rows = (
                        await session.execute(
                            text(
                                "SELECT content FROM kb_chunks "
                                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
                            ),
                            {"q": _vector_to_pg(qvec), "k": top_k},
                        )
                    ).all()
                    if rows:
                        return [r[0] for r in rows]
                except Exception:
                    pass
            # The PostgreSQL path may have left the session in an aborted
            # (invalidated) transaction; roll it back before the in-Python
            # fallback so the SELECT below still executes.
            try:
                await session.rollback()
            except Exception:
                pass
            rows = (await session.execute(text("SELECT content, embedding FROM kb_chunks"))).all()

        scored = []
        for content, emb in rows:
            vec = _parse_vector(emb)
            if vec is None:
                continue
            scored.append((cosine_similarity(qvec, vec), content))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _read_file(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return _read_pdf(path)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF. Best-effort; returns '' if no PDF lib present."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from pdfminer.high_level import extract_text  # type: ignore
        except ImportError:
            return ""
        try:
            return extract_text(str(path)) or ""
        except Exception:
            return ""
    try:
        reader = PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(parts)
    except Exception:
        return ""


__all__ = ["KnowledgeBase", "chunk_text"]
