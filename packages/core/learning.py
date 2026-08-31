"""Learning Engine (Phase 2, ADR-010).

Closes the loop: feedback (human ratings + auto-critique) -> dynamic routing
rules -> periodic learning cycle report. Rules learned here are consumed by
RouterAgent via `dynamic_rules` so routing improves over time without code
changes. Knowledge-worthy comments can be ingested into the RAG pipeline.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from packages.observability.logging import get_logger

logger = get_logger("learning")

# Learned rules are stored as a JSON file (pilot scale); swap to a DB table
# without changing RouterAgent's consumption interface.
RULES_PATH = Path("data/learned_routing_rules.json")


class DynamicRule(BaseModel):
    """A routing rule learned from user corrections.

    keyword -> capability. Applied by RouterAgent BEFORE static rule
    fallbacks, so user corrections win.
    """

    keyword: str = Field(min_length=2)
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*$")
    hits: int = 1
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class LearningEngine:
    """Records feedback, derives dynamic routing rules, runs the cycle."""

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules_path = rules_path or RULES_PATH
        self._rules: list[DynamicRule] = self._load_rules()

    # -- persistence ---------------------------------------------------------
    def _load_rules(self) -> list[DynamicRule]:
        try:
            if self.rules_path.exists():
                data = json.loads(self.rules_path.read_text(encoding="utf-8"))
                return [DynamicRule(**item) for item in data]
        except Exception as exc:  # noqa: BLE001
            logger.warning("rules_load_failed", extra={"error": str(exc)})
        return []

    def _save_rules(self) -> None:
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.rules_path.write_text(
            json.dumps([r.model_dump() for r in self._rules], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- feedback ------------------------------------------------------------
    async def record_feedback(self, feedback: dict[str, Any]) -> None:
        """Process one feedback record: learn corrected_capability -> rule."""
        corrected = feedback.get("corrected_capability")
        comment = feedback.get("comment") or ""
        if corrected and comment:
            words = [w.lower() for w in re.findall(r"[a-zà-ỹ]{4,}", comment, re.IGNORECASE)]
            keyword = words[0] if words else None
            if keyword:
                self.learn_rule(keyword, corrected)

    def learn_rule(self, keyword: str, capability: str) -> None:
        for rule in self._rules:
            if rule.keyword == keyword:
                rule.hits += 1
                rule.capability = capability
                rule.updated_at = datetime.now(UTC).isoformat()
                break
        else:
            self._rules.append(DynamicRule(keyword=keyword, capability=capability))
        self._save_rules()
        logger.info("rule_learned", extra={"keyword": keyword, "capability": capability})

    def get_rules(self) -> list[DynamicRule]:
        return list(self._rules)

    # -- cycle ----------------------------------------------------------------
    async def run_cycle(self, feedback_batch: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Daily learning cycle: aggregate feedback, refresh rules, report.

        `feedback_batch` comes from the task_feedback store when persistence
        is enabled; without it the cycle only reports the current rule set.
        """
        ratings = Counter(f.get("rating") for f in (feedback_batch or []) if f.get("rating"))
        for f in feedback_batch or []:
            await self.record_feedback(f)
        # Feedback-rate metric so the friendly-feedback loop is measurable.
        try:
            from packages.observability.metrics import get_metrics

            metrics = get_metrics()
            metrics.incr("feedback_total", kind="cycle")
            for rating_value, count in ratings.items():
                metrics.incr("feedback_total", value=count, kind="cycle", rating=rating_value)
        except Exception:  # noqa: BLE001 — metrics must never break learning
            pass
        return {
            "ran_at": datetime.now(UTC).isoformat(),
            "feedback_count": len(feedback_batch or []),
            "ratings": dict(ratings),
            "rules_total": len(self._rules),
            "top_rules": [
                {"keyword": r.keyword, "capability": r.capability, "hits": r.hits}
                for r in sorted(self._rules, key=lambda r: -r.hits)[:10]
            ],
        }


__all__ = ["LearningEngine", "DynamicRule", "RULES_PATH"]
