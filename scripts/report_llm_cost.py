# -*- coding: utf-8 -*-
"""LLM cost report (AI-Engineer point 3 observability).

Reads the JSONL usage ledger produced by packages.core.llm_cost and prints a
human-readable summary: totals, cache-hit rate, per-model breakdown, top tags.

Usage:
    python scripts/report_llm_cost.py [path/to/llm_usage.jsonl]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT = Path("data/llm_usage.jsonl")


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    rows = _load(path)
    if not rows:
        print(f"No usage records found at {path}")
        return 0

    total_calls = len(rows)
    cache_hits = sum(1 for r in rows if r.get("cache_hit"))
    in_tok = sum(r.get("in_tokens", 0) for r in rows)
    out_tok = sum(r.get("out_tokens", 0) for r in rows)
    cost = sum(r.get("est_cost_usd", 0.0) for r in rows)
    lat = sum(r.get("latency_s", 0.0) for r in rows)

    by_model = defaultdict(lambda: {"calls": 0, "hit": 0, "in": 0, "out": 0, "cost": 0.0, "lat": 0.0})
    by_tag = defaultdict(lambda: {"calls": 0, "hit": 0})
    for r in rows:
        m = by_model[r.get("model", "unknown")]
        m["calls"] += 1
        m["hit"] += 1 if r.get("cache_hit") else 0
        m["in"] += r.get("in_tokens", 0)
        m["out"] += r.get("out_tokens", 0)
        m["cost"] += r.get("est_cost_usd", 0.0)
        m["lat"] += r.get("latency_s", 0.0)
        t = by_tag[r.get("tag", "untagged")]
        t["calls"] += 1
        t["hit"] += 1 if r.get("cache_hit") else 0

    print("=" * 60)
    print("LLM COST REPORT")
    print("=" * 60)
    print(f"Ledger       : {path}")
    print(f"Total calls  : {total_calls}")
    print(f"Cache hits   : {cache_hits} ({100*cache_hits/max(1,total_calls):.1f}%)")
    print(f"Tokens in/out: {in_tok:,} / {out_tok:,}")
    print(f"Est. cost    : ${cost:.4f}")
    print(f"Total latency: {lat:.1f}s (saved {100*cache_hits/max(1,total_calls):.1f}% via cache)")
    print("-" * 60)
    print("Per model:")
    for m, d in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        print(f"  {m:<22} calls={d['calls']:>4} hit={d['hit']:>3} "
              f"tok={d['in']+d['out']:>7,} ${d['cost']:.4f}")
    print("-" * 60)
    print("Per tag:")
    for t, d in sorted(by_tag.items(), key=lambda kv: -kv[1]["calls"]):
        print(f"  {t:<22} calls={d['calls']:>4} hit={d['hit']:>3}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
