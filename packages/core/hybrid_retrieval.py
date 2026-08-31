"""Hybrid retrieval: merge and rerank FTS + vector results (Feature 1).

Both the full-text and the vector retrievers return ranked lists of items.
This helper fuses them into a single reranked list using Reciprocal Rank
Fusion (RRF), so a chunk that ranks well on *either* signal surfaces while
chunks strong on both rank highest. RRF is rank-based (not score-based), which
avoids brittle min-max normalization across heterogeneous score scales.
"""

from __future__ import annotations

from typing import Any, Iterable

_RRF_K = 60  # RRF constant: dampens the contribution of very deep ranks.


def _item_key(item: dict[str, Any]) -> Any:
    """Stable identity for a result so FTS and vector entries can be merged.

    Prefers an explicit ``id`` or ``chunk_index``; otherwise falls back to a
    hash of ``content`` so identical chunks from both sources still merge (a
    stable key, not a raw full-text comparison).
    """
    if item.get("id") is not None:
        return ("id", item["id"])
    if item.get("chunk_index") is not None:
        return ("chunk_index", item["chunk_index"])
    if item.get("content") is not None:
        return ("content", hash(item["content"]))
    return ("obj", id(item))


def _rrf(rank: int, weight: float) -> float:
    """Reciprocal Rank Fusion contribution for ``rank`` (1-based) under ``weight``."""
    return weight / (_RRF_K + rank)


def hybrid_retrieve(
    query: str,
    fts_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    fts_weight: float = 0.5,
    vector_weight: float = 0.5,
) -> list[dict[str, Any]]:
    """Merge ``fts_results`` and ``vector_results`` and rerank by RRF score.

    Each input is a list of dicts carrying at least an ``id``/``chunk_index``
    key (or ``content``) and a numeric ``score``. Each occurrence contributes
    ``weight / (k + rank)`` (rank 1 = best). Items present in only one source
    still contribute. The merged list is returned sorted by the combined RRF
    score (descending).

    ``query`` is accepted for API symmetry / logging and is currently unused in
    the scoring math (the per-source ranks already encode relevance).
    """
    total_weight = fts_weight + vector_weight
    if total_weight <= 0:
        total_weight = 1.0

    merged: dict[Any, dict[str, Any]] = {}
    for results, weight in ((fts_results, fts_weight), (vector_results, vector_weight)):
        for rank, item in enumerate(results, start=1):
            key = _item_key(item)
            # Start from a copy that drops any incoming "score" so the fused
            # score is derived purely from RRF ranks (not the inputs' scales).
            entry = merged.setdefault(key, {k: v for k, v in item.items() if k != "score"})
            entry["score"] = entry.get("score", 0.0) + _rrf(rank, weight)

    for entry in merged.values():
        entry["score"] = entry["score"] / total_weight

    results: list[dict[str, Any]] = [dict(item) for item in merged.values()]
    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results
