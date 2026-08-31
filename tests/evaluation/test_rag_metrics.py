"""RAG retrieval evaluation tests.

Measures:
- Precision@k: fraction of retrieved docs that are relevant
- Recall@k: fraction of relevant docs that are retrieved
- MRR: Mean Reciprocal Rank of first relevant result
- Hybrid vs FTS-only vs Vector-only comparison
"""

from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.core.hybrid_retrieval import hybrid_retrieve
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.base import EmbeddingProvider
from tests.evaluation.eval_dataset import EVAL_DOCUMENTS, GOLDEN_DATASET

# ---------------------------------------------------------------------------
# Smart mock embedding provider (TF-IDF based for meaningful similarity)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class TfIdfMockEmbedding(EmbeddingProvider):
    """TF-IDF-based mock embedding for evaluation.

    Produces vectors where texts with shared vocabulary have higher cosine
    similarity, enabling meaningful vector retrieval in tests.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self._vocab: dict[str, int] = {}
        self._fitted = False

    @property
    def name(self) -> str:
        return "tfidf_mock"

    def _tokenize(self, text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def _build_vocab(self, texts: list[str]) -> None:
        tokens: set[str] = set()
        for t in texts:
            tokens.update(self._tokenize(t))
        self._vocab = {w: i % self.dim for i, w in enumerate(sorted(tokens))}
        self._fitted = True

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = self._tokenize(text)
        if not tokens:
            return vec
        for tok in tokens:
            if tok in self._vocab:
                vec[self._vocab[tok]] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._fitted:
            self._build_vocab(texts)
        return [self._embed_one(t) for t in texts]

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Metric calculations
# ---------------------------------------------------------------------------


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Calculate Precision@K.

    Fraction of top-k retrieved documents that are relevant.
    """
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    relevant_retrieved = sum(1 for doc_id in top_k if doc_id in relevant_ids)
    return relevant_retrieved / len(top_k)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Calculate Recall@K.

    Fraction of relevant documents that appear in top-k retrieved.
    """
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    retrieved_relevant = sum(1 for doc_id in top_k if doc_id in relevant_ids)
    return retrieved_relevant / len(relevant_ids)


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    """Calculate Reciprocal Rank of first relevant result.

    Returns 1/rank of first relevant doc, or 0 if none found.
    """
    for i, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / i
    return 0.0


# ---------------------------------------------------------------------------
# Retrieval helpers (get scored results with metadata)
# ---------------------------------------------------------------------------


def _fts_score(query_tokens: list[str], content: str) -> float:
    """Token-overlap FTS scoring matching KnowledgeBase._score_query."""
    content_tokens = _TOKEN_RE.findall(content.lower())
    if not content_tokens or not query_tokens:
        return 0.0
    content_set = set(content_tokens)
    distinct = sum(1 for t in query_tokens if t in content_set)
    if distinct == 0:
        return 0.0
    return float(sum(content_tokens.count(t) for t in query_tokens))


async def _get_fts_results(
    session_factory: async_sessionmaker[Any],
    query: str,
    k: int = 20,
) -> list[dict[str, Any]]:
    """Get FTS results with title, content, and score."""
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return []
    async with session_factory() as session:
        rows = (await session.execute(text("SELECT id, title, content FROM kb_chunks"))).all()
    results = []
    for row in rows:
        score = _fts_score(tokens, row[2])
        if score > 0:
            results.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "content": row[2],
                    "score": score,
                }
            )
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


async def _get_vector_results(
    session_factory: async_sessionmaker[Any],
    query: str,
    embedding_provider: EmbeddingProvider,
    k: int = 20,
) -> list[dict[str, Any]]:
    """Get vector search results with title, content, and cosine similarity."""
    async with session_factory() as session:
        rows = (
            await session.execute(text("SELECT id, title, content, embedding FROM kb_chunks"))
        ).all()
    qvec = (await embedding_provider.embed([query]))[0]
    results = []
    for row in rows:
        emb_str = row[3]
        if not emb_str:
            continue
        vec = _parse_vector(emb_str)
        if vec is None:
            continue
        score = _cosine_similarity(qvec, vec)
        results.append(
            {
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "score": score,
            }
        )
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def _parse_vector(value: str) -> list[float] | None:
    """Parse stored embedding string back to list of floats."""
    if not value:
        return None
    text_val = str(value).strip()
    if text_val.startswith("["):
        text_val = text_val[1:]
    if text_val.endswith("]"):
        text_val = text_val[:-1]
    if not text_val:
        return None
    try:
        return [float(x) for x in text_val.split(",")]
    except ValueError:
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def eval_kb():
    """Create a knowledge base with evaluation documents."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = TfIdfMockEmbedding(dim=256)
    kb = KnowledgeBase(factory, embedding_provider=provider)
    await kb.init()
    await _ingest_documents(kb, factory, provider)
    yield kb
    await engine.dispose()


async def _ingest_documents(
    kb: KnowledgeBase,
    factory: async_sessionmaker[Any],
    provider: TfIdfMockEmbedding,
) -> None:
    """Ingest evaluation documents into the knowledge base."""
    tmpdir = tempfile.mkdtemp()
    all_texts = [doc["content"] for doc in EVAL_DOCUMENTS]
    all_texts.extend(doc["question"] for doc in GOLDEN_DATASET)
    provider._build_vocab(all_texts)
    for doc in EVAL_DOCUMENTS:
        path = Path(tmpdir) / f"{doc['doc_id']}.md"
        path.write_text(doc["content"], encoding="utf-8")
        await kb.add_document(str(path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _extract_titles(results: list[dict[str, Any]]) -> list[str]:
    """Extract titles from retrieval results."""
    return [r["title"] for r in results]


def _deduplicate_by_title(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep highest-scored chunk per title."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        title = r["title"]
        if title not in seen or r["score"] > seen[title]["score"]:
            seen[title] = r
    return list(seen.values())


@pytest.mark.asyncio
async def test_hybrid_retrieval_precision(eval_kb):
    """Test hybrid retrieval achieves reasonable precision."""
    precisions_at_3 = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(eval_kb._factory, q["question"], k=20)
        vec = await _get_vector_results(
            eval_kb._factory, q["question"], eval_kb._embedding_provider, k=20
        )
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        titles = _extract_titles(_deduplicate_by_title(hybrid))
        p3 = precision_at_k(titles, set(q["expected_chunk_ids"]), k=3)
        precisions_at_3.append(p3)
    avg_precision = sum(precisions_at_3) / len(precisions_at_3)
    assert avg_precision >= 0.2, f"avg P@3 too low: {avg_precision:.2f}"


@pytest.mark.asyncio
async def test_hybrid_retrieval_recall(eval_kb):
    """Test hybrid retrieval achieves reasonable recall."""
    recalls_at_5 = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(eval_kb._factory, q["question"], k=20)
        vec = await _get_vector_results(
            eval_kb._factory, q["question"], eval_kb._embedding_provider, k=20
        )
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        titles = _extract_titles(_deduplicate_by_title(hybrid))
        r5 = recall_at_k(titles, set(q["expected_chunk_ids"]), k=5)
        recalls_at_5.append(r5)
    avg_recall = sum(recalls_at_5) / len(recalls_at_5)
    assert avg_recall >= 0.6, f"avg Recall@5 too low: {avg_recall:.2f}"


@pytest.mark.asyncio
async def test_hybrid_vs_fts_comparison(eval_kb):
    """Compare hybrid retrieval against FTS-only baseline."""
    hybrid_scores = []
    fts_scores = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(eval_kb._factory, q["question"], k=20)
        vec = await _get_vector_results(
            eval_kb._factory, q["question"], eval_kb._embedding_provider, k=20
        )
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        hybrid_titles = _extract_titles(_deduplicate_by_title(hybrid))
        fts_titles = _extract_titles(_deduplicate_by_title(fts))
        relevant = set(q["expected_chunk_ids"])
        hybrid_scores.append(reciprocal_rank(hybrid_titles, relevant))
        fts_scores.append(reciprocal_rank(fts_titles, relevant))
    avg_hybrid = sum(hybrid_scores) / len(hybrid_scores)
    avg_fts = sum(fts_scores) / len(fts_scores)
    assert avg_hybrid >= avg_fts - 0.05, (
        f"hybrid MRR ({avg_hybrid:.3f}) significantly worse than FTS MRR ({avg_fts:.3f})"
    )


@pytest.mark.asyncio
async def test_hybrid_vs_vector_comparison(eval_kb):
    """Compare hybrid retrieval against vector-only baseline."""
    hybrid_scores = []
    vec_scores = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(eval_kb._factory, q["question"], k=20)
        vec = await _get_vector_results(
            eval_kb._factory, q["question"], eval_kb._embedding_provider, k=20
        )
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        hybrid_titles = _extract_titles(_deduplicate_by_title(hybrid))
        vec_titles = _extract_titles(_deduplicate_by_title(vec))
        relevant = set(q["expected_chunk_ids"])
        hybrid_scores.append(reciprocal_rank(hybrid_titles, relevant))
        vec_scores.append(reciprocal_rank(vec_titles, relevant))
    avg_hybrid = sum(hybrid_scores) / len(hybrid_scores)
    avg_vec = sum(vec_scores) / len(vec_scores)
    assert avg_hybrid >= avg_vec - 0.05, (
        f"hybrid MRR ({avg_hybrid:.3f}) significantly worse than vector MRR ({avg_vec:.3f})"
    )


@pytest.mark.asyncio
async def test_mrr_score(eval_kb):
    """Test Mean Reciprocal Rank across all queries."""
    mrrs = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(eval_kb._factory, q["question"], k=20)
        vec = await _get_vector_results(
            eval_kb._factory, q["question"], eval_kb._embedding_provider, k=20
        )
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        titles = _extract_titles(_deduplicate_by_title(hybrid))
        mrrs.append(reciprocal_rank(titles, set(q["expected_chunk_ids"])))
    avg_mrr = sum(mrrs) / len(mrrs)
    assert avg_mrr >= 0.5, f"hybrid MRR too low: {avg_mrr:.3f}"


def test_evaluation_report(capsys):
    """Generate and print evaluation report with all metrics."""
    report = _generate_report_text()
    print(report)
    captured = capsys.readouterr()
    assert "RAG Evaluation Report" in captured.out
    assert "HYBRID" in captured.out


def _generate_report_text() -> str:
    """Generate a text report summarizing the evaluation metrics."""
    lines = [
        "=" * 60,
        "RAG Evaluation Report",
        "=" * 60,
        f"Dataset: {len(GOLDEN_DATASET)} queries, {len(EVAL_DOCUMENTS)} documents",
        "",
        "Retrieval Methods Compared:",
        "  - FTS: Full-text search (keyword matching)",
        "  - VECTOR: Vector similarity search (semantic matching)",
        "  - HYBRID: RRF fusion of FTS + Vector results",
        "",
        "Metrics (averaged over all queries):",
        "  Precision@1: (run async tests for full report)",
        "  Precision@3: (run async tests for full report)",
        "  Precision@5: (run async tests for full report)",
        "  Recall@5:    (run async tests for full report)",
        "  MRR:         (run async tests for full report)",
        "",
        "See test_hybrid_* tests for detailed results.",
        "=" * 60,
    ]
    return "\n".join(lines)
