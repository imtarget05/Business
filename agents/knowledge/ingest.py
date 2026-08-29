"""Knowledge ingestion helpers for the full-text Second Brain (Task 1).

Replaces the old embedding-based pipeline: chunks are stored verbatim in
và truy xuất (retrieval) được thực hiện bằng PostgreSQL tsvector full-text
(or in-Python scoring on SQLite). No embedding model is involved.

Public API:
- :func:`chunk_text` — deterministic word-overlap chunking.
- :meth:`KnowledgeBase.add_document` / :meth:`KnowledgeBase.index_directory`
  — load + chunk + store a file / a whole ``data/kb`` directory.
"""

from __future__ import annotations

from pathlib import Path

from packages.core.knowledge_base import KnowledgeBase, chunk_text

__all__ = ["KnowledgeBase", "chunk_text", "index_file", "index_directory"]


async def index_file(kb: KnowledgeBase, path: str | Path) -> dict:
    """Index a single file into the knowledge base."""
    return await kb.add_document(path)


async def index_directory(kb: KnowledgeBase, path: str | Path) -> int:
    """Index every ``.md`` / ``.txt`` / ``.pdf`` under ``path``.

    Returns the number of documents indexed.
    """
    return await kb.index_directory(path)
