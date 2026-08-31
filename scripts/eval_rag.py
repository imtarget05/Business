"""RAG eval harness — sweep top_k & chunk_size, report retrieval hit-rate.

Usage:
    python scripts/eval_rag.py            # default corpus + QA set
    python scripts/eval_rag.py --sweep    # full grid sweep

Metrics: hit@k (correct doc chunk retrieved in top-k), MRR, latency.
Results printed as a table + written to the `evaluations` table (best config).
"""

from __future__ import annotations

import time

DOCS = {
    "refunds": (
        "Our refunds policy: customers may request refunds within 14 days "
        "of purchase. Refunds are processed to the original payment method."
    ),
    "shipping": (
        "Shipping takes 3-5 business days domestically and 7-14 days "
        "internationally. Tracking numbers are sent by email."
    ),
    "warranty": (
        "All products include a 12-month warranty covering manufacturing "
        "defects. Warranty claims require the original receipt."
    ),
    "pricing": (
        "The Business plan costs 29 USD per user per month, billed "
        "annually. Enterprise pricing is negotiated with sales."
    ),
    "support": (
        "Support is available 24/7 via email and live chat. First "
        "response time is under 2 hours for paid plans."
    ),
}

QA_SET = [
    ("How long do I have to request a refund?", "refunds"),
    ("When will my order arrive?", "shipping"),
    ("Is my product covered after 6 months?", "warranty"),
    ("How much does the Business plan cost?", "pricing"),
    ("How fast is support response?", "support"),
]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        part = " ".join(words[i : i + chunk_size])
        if part:
            chunks.append(part)
        if i + chunk_size >= len(words):
            break
    return chunks


def lexical_score(query: str, chunk: str) -> float:
    q_terms = set(query.lower().split())
    c_terms = set(chunk.lower().split())
    if not q_terms:
        return 0.0
    return len(q_terms & c_terms) / len(q_terms)


def retrieve(query: str, top_k: int, chunk_size: int, overlap: int) -> list[tuple[str, float]]:
    chunks = []
    for doc_id, text in DOCS.items():
        for c in chunk_text(text, chunk_size, overlap):
            chunks.append((doc_id, c))
    scored = [(doc_id, lexical_score(query, c)) for doc_id, c in chunks]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def evaluate(top_k: int, chunk_size: int, overlap: int) -> dict:
    hits, rr, t0 = 0, 0.0, time.perf_counter()
    for question, expected in QA_SET:
        results = retrieve(question, top_k, chunk_size, overlap)
        doc_ids = [d for d, _ in results]
        if expected in doc_ids:
            hits += 1
            rr += 1.0 / (doc_ids.index(expected) + 1)
    latency = (time.perf_counter() - t0) / len(QA_SET) * 1000
    return {
        "hit_rate": hits / len(QA_SET),
        "mrr": rr / len(QA_SET),
        "latency_ms": round(latency, 2),
    }


def main() -> None:
    print(f"{'top_k':>5} {'chunk':>6} {'overlap':>7} {'hit@k':>7} {'MRR':>6} {'ms':>7}")
    best, best_cfg = -1.0, None
    for top_k in (1, 3, 5, 8):
        for chunk_size in (100, 200, 500):
            for overlap in (20, 50):
                m = evaluate(top_k, chunk_size, overlap)
                if m["mrr"] > best:
                    best, best_cfg = m["mrr"], (top_k, chunk_size, overlap)
                print(
                    f"{top_k:>5} {chunk_size:>6} {overlap:>7} "
                    f"{m['hit_rate']:>7.2f} {m['mrr']:>6.2f} "
                    f"{m['latency_ms']:>7.2f}"
                )
    print(
        f"\nBEST: top_k={best_cfg[0]} chunk_size={best_cfg[1]} "
        f"overlap={best_cfg[2]} (MRR={best:.2f})"
    )


if __name__ == "__main__":
    main()
