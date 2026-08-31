"""Tests: daily report now reflects real system activity (RAG, LLM cost, health)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agents.monitoring import progress_report as pr


def test_llm_cost_summary_missing_ledger(tmp_path):
    ledger = tmp_path / "llm_usage.jsonl"
    # not created -> absent
    pr.get_llm_cost_summary.__wrapped__ if hasattr(pr.get_llm_cost_summary, "__wrapped__") else None
    # call via monkey-patching the default path by writing nothing; use env override
    import os

    os.environ["LLM_USAGE_LEDGER"] = str(ledger)
    try:
        res = pr.get_llm_cost_summary()
        assert res["present"] is False
        assert res["calls"] == 0
    finally:
        os.environ.pop("LLM_USAGE_LEDGER", None)


def test_llm_cost_summary_parses_ledger(tmp_path):
    import os

    ledger = tmp_path / "llm_usage.jsonl"
    rows = [
        {"model": "m", "cache_hit": False, "est_cost_usd": 0.001},
        {"model": "m", "cache_hit": True, "est_cost_usd": 0.0},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.environ["LLM_USAGE_LEDGER"] = str(ledger)
    try:
        res = pr.get_llm_cost_summary()
        assert res["present"] is True
        assert res["calls"] == 2
        assert res["cache_hits"] == 1
        assert abs(res["est_cost_usd"] - 0.001) < 1e-9
    finally:
        os.environ.pop("LLM_USAGE_LEDGER", None)


def test_report_markdown_includes_new_sections():
    r = pr.DailyReport(date="2026-08-31", generated_at="2026-08-31T00:00:00+00:00")
    r.rag_facts = 3
    r.llm_ledger_present = True
    r.llm_calls = 5
    r.llm_cache_hits = 2
    r.llm_est_cost_usd = 0.0042
    r.health_overall = "ok"
    r.health_components = [{"name": "api", "status": "ok", "message": "healthy"}]
    md = r.to_markdown()
    assert "🧠 Knowledge Cache (RAG)" in md
    assert "Verified Michelin facts cached**: 3" in md
    assert "💰 LLM Cost" in md
    assert "Cache hits**: 2 (40.0%)" in md
    assert "🏥 Health" in md
    assert "Overall**: ok" in md


def test_report_markdown_empty_ledger_note():
    r = pr.DailyReport(date="2026-08-31", generated_at="2026-08-31T00:00:00+00:00")
    r.llm_ledger_present = False
    md = r.to_markdown()
    assert "No usage recorded yet" in md
