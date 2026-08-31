# -*- coding: utf-8 -*-
"""LLM cost + prompt-cache tracking (point 3 of the AI-Engineer skill stack:

Prompt Compression + Caching to optimize AI cost).

This is a thin, dependency-free layer that business code calls *around* the
LLMProvider. It does NOT change the provider contract (ADR-005) — it only:
  * estimates token usage (no SDK changes needed),
  * appends a JSONL ledger (``llm_usage.jsonl``) for cost observability,
  * short-circuits repeated identical prompts via a small on-disk cache.

Estimates are intentionally simple and conservative: ~4 chars per token for
mixed Vietnamese/English text. Real billing comes from the provider; this is
for trend/regression spotting, not invoicing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# Reference prices (USD per 1K tokens) — conservative public list prices.
# Update as providers change; values are for observability only.
_PRICE_PER_1K = {
    "qwen3:1.7b": {"in": 0.0, "out": 0.0},          # self-hosted Ollama -> free
    "qwen2.5": {"in": 0.0, "out": 0.0},              # self-hosted -> free
    "hy3-free": {"in": 0.0, "out": 0.0},             # free tier
    "minimax": {"in": 0.0, "out": 0.0},              # free tier
    "default": {"in": 0.001, "out": 0.002},          # cloud fallback estimate
}

_CACHE_DIR = Path(os.environ.get("LLM_CACHE_DIR", "data/llm_cache"))
_LEDGER = Path(os.environ.get("LLM_USAGE_LEDGER", "data/llm_usage.jsonl"))
_CACHE_TTL_S = int(os.environ.get("LLM_CACHE_TTL_S", "3600"))


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token (mixed vi/en)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _price_for(model: str) -> dict:
    for key, price in _PRICE_PER_1K.items():
        if key in (model or "").lower():
            return price
    return _PRICE_PER_1K["default"]


def log_llm_usage(
    model: str,
    prompt: str,
    completion: str,
    latency_s: float,
    *,
    cache_hit: bool = False,
    tag: str = "",
) -> dict:
    """Append one usage record to the ledger and return it.

    Safe to call anywhere; never raises (logging must not break the bot).
    """
    rec = {
        "ts": time.time(),
        "model": model,
        "tag": tag,
        "cache_hit": cache_hit,
        "in_tokens": estimate_tokens(prompt),
        "out_tokens": estimate_tokens(completion),
        "latency_s": round(latency_s, 3),
    }
    price = _price_for(model)
    rec["est_cost_usd"] = round(
        (rec["in_tokens"] / 1000) * price["in"]
        + (rec["out_tokens"] / 1000) * price["out"],
        6,
    )
    try:
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with _LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def prompt_cache_key(prompt: str, system: str = "") -> str:
    return hashlib.sha256(f"{system}|||{prompt}".encode("utf-8")).hexdigest()


def prompt_cache_get(key: str) -> str | None:
    """Return cached completion if fresh, else None."""
    try:
        path = _CACHE_DIR / f"{key}.txt"
        if not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > _CACHE_TTL_S:
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def prompt_cache_set(key: str, completion: str) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.txt").write_text(completion, encoding="utf-8")
    except Exception:
        pass
