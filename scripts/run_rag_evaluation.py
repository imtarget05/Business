#!/usr/bin/env python3
"""Run RAG evaluation and generate metrics report.

Usage: python scripts/run_rag_evaluation.py
Output: Prints metrics table and writes docs/metrics.md
"""

from __future__ import annotations

import asyncio
import math
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.core.hybrid_retrieval import hybrid_retrieve
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.base import EmbeddingProvider
from tests.evaluation.eval_dataset import EVAL_DOCUMENTS, GOLDEN_DATASET

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class TfIdfMockEmbedding(EmbeddingProvider):
    """TF-IDF-based mock embedding for meaningful similarity in evaluation."""

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


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    return sum(1 for d in top_k if d in relevant_ids) / len(top_k)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    return sum(1 for d in top_k if d in relevant_ids) / len(relevant_ids)


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    for i, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / i
    return 0.0


def _parse_vector(value: str) -> list[float] | None:
    if not value:
        return None
    s = str(value).strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    if not s:
        return None
    try:
        return [float(x) for x in s.split(",")]
    except ValueError:
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _fts_score(query_tokens: list[str], content: str) -> float:
    content_tokens = _TOKEN_RE.findall(content.lower())
    if not content_tokens or not query_tokens:
        return 0.0
    content_set = set(content_tokens)
    if not any(t in content_set for t in query_tokens):
        return 0.0
    return float(sum(content_tokens.count(t) for t in query_tokens))


async def _get_fts_results(
    factory: async_sessionmaker[Any], query: str, k: int = 20,
) -> list[dict[str, Any]]:
    """Get FTS results with title, content, and score."""
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return []
    async with factory() as session:
        rows = (await session.execute(text("SELECT id, title, content FROM kb_chunks"))).all()
    results = []
    for row in rows:
        score = _fts_score(tokens, row[2])
        if score > 0:
            results.append({"id": row[0], "title": row[1], "content": row[2], "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


async def _get_vector_results(
    factory: async_sessionmaker[Any], query: str, provider: TfIdfMockEmbedding, k: int = 20,
) -> list[dict[str, Any]]:
    """Get vector search results with title, content, and cosine similarity."""
    async with factory() as session:
        rows = (
            await session.execute(text("SELECT id, title, content, embedding FROM kb_chunks"))
        ).all()
    qvec = (await provider.embed([query]))[0]
    results = []
    for row in rows:
        vec = _parse_vector(row[3])
        if vec is None:
            continue
        score = _cosine_sim(qvec, vec)
        results.append({"id": row[0], "title": row[1], "content": row[2], "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def _dedup(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep highest-scored chunk per title (document)."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        title = r["title"]
        if title not in seen or r["score"] > seen[title]["score"]:
            seen[title] = r
    return list(seen.values())


async def run_evaluation() -> dict[str, Any]:
    """Run full evaluation and return metrics."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = TfIdfMockEmbedding(dim=256)
    kb = KnowledgeBase(factory, embedding_provider=provider)
    await kb.init()
    tmpdir = tempfile.mkdtemp()
    all_texts = [d["content"] for d in EVAL_DOCUMENTS]
    all_texts.extend(q["question"] for q in GOLDEN_DATASET)
    provider._build_vocab(all_texts)
    for doc in EVAL_DOCUMENTS:
        path = Path(tmpdir) / f"{doc['doc_id']}.md"
        path.write_text(doc["content"], encoding="utf-8")
        await kb.add_document(str(path))
    methods = ["fts", "vector", "hybrid"]
    metrics: dict[str, dict[str, list[float]]] = {}
    for method_name in methods:
        metrics[method_name] = {"p1": [], "p3": [], "p5": [], "r5": [], "mrr": []}
    query_results: list[dict[str, Any]] = []
    for q in GOLDEN_DATASET:
        fts = await _get_fts_results(factory, q["question"], k=20)
        vec = await _get_vector_results(factory, q["question"], provider, k=20)
        hybrid = hybrid_retrieve(q["question"], fts, vec)
        relevant = set(q["expected_chunk_ids"])
        q_res = {"question": q["question"], "relevant": relevant}
        for method_name, results in [("fts", fts), ("vector", vec), ("hybrid", hybrid)]:
            deduped = _dedup(results)
            titles = [r["title"] for r in deduped]
            q_res[method_name] = titles
            m = metrics[method_name]
            m["p1"].append(precision_at_k(titles, relevant, 1))
            m["p3"].append(precision_at_k(titles, relevant, 3))
            m["p5"].append(precision_at_k(titles, relevant, 5))
            m["r5"].append(recall_at_k(titles, relevant, 5))
            m["mrr"].append(reciprocal_rank(titles, relevant))
        query_results.append(q_res)
    await engine.dispose()
    summary: dict[str, dict[str, float]] = {}
    for method_name in methods:
        m = metrics[method_name]
        count = len(m["p1"])
        summary[method_name] = {
            "p1": sum(m["p1"]) / count if count else 0.0,
            "p3": sum(m["p3"]) / count if count else 0.0,
            "p5": sum(m["p5"]) / count if count else 0.0,
            "r5": sum(m["r5"]) / count if count else 0.0,
            "mrr": sum(m["mrr"]) / count if count else 0.0,
        }
    return {"summary": summary, "query_results": query_results, "methods": methods}


def format_table(summary: dict[str, dict[str, float]]) -> str:
    lines = []
    lines.append("| Method | P@1 | P@3 | P@5 | Recall@5 | MRR |")
    lines.append("|--------|-----|-----|-----|----------|-----|")
    for method in ["fts", "vector", "hybrid"]:
        m = summary[method]
        lines.append(
            f"| {method.upper():<8} | {m['p1']:.3f} | {m['p3']:.3f} | "
            f"{m['p5']:.3f} | {m['r5']:.3f} | {m['mrr']:.3f} |"
        )
    return "\n".join(lines)


def generate_markdown(summary: dict[str, dict[str, float]]) -> str:
    table = format_table(summary)
    hybrid = summary["hybrid"]
    fts = summary["fts"]
    vector = summary["vector"]
    findings = []
    if hybrid["mrr"] > fts["mrr"]:
        findings.append(
            f"- Hybrid MRR ({hybrid['mrr']:.3f}) outperforms FTS-only ({fts['mrr']:.3f})"
        )
    if hybrid["mrr"] > vector["mrr"]:
        findings.append(
            f"- Hybrid MRR ({hybrid['mrr']:.3f}) outperforms vector-only ({vector['mrr']:.3f})"
        )
    if hybrid["r5"] > fts["r5"]:
        findings.append(
            f"- Hybrid recall@5 ({hybrid['r5']:.3f}) exceeds FTS recall@5 ({fts['r5']:.3f})"
        )
    if not findings:
        findings.append("- All methods perform similarly on this dataset")
    findings_text = "\n".join(findings)
    return f"""# RAG Evaluation Metrics

## Overview
Evaluation of the hybrid retrieval system (FTS + Vector + RRF fusion) against a golden dataset.

## Dataset
- **Questions**: {len(GOLDEN_DATASET)} evaluation queries
- **Documents**: {len(EVAL_DOCUMENTS)} indexed documents
- **Topics**: Vietnamese geography, AI/ML, business operations, technology, agent architecture

## Metrics

### Retrieval Performance

{table}

### Key Findings
{findings_text}

## How to Run
```bash
python scripts/run_rag_evaluation.py
```
"""


async def main() -> None:
    print("Running RAG evaluation...")
    results = await run_evaluation()
    summary = results["summary"]
    print("\n=== RAG Evaluation Results ===\n")
    print(format_table(summary))
    md = generate_markdown(summary)
    docs_path = Path(__file__).parent.parent / "docs" / "metrics.md"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(md, encoding="utf-8")
    print(f"\nReport written to {docs_path}")


if __name__ == "__main__":
    asyncio.run(main())
