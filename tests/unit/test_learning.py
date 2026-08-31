"""Unit tests: learning engine + reflection (ADR-010)."""

from __future__ import annotations

from pathlib import Path

from packages.core.learning import LearningEngine
from packages.core.reflection import ReflectionEngine
from packages.llm.mock import MockLLMProvider


class TestLearningEngine:
    async def test_learn_rule_and_persist(self, tmp_path: Path) -> None:
        eng = LearningEngine(rules_path=tmp_path / "rules.json")
        await eng.record_feedback(
            {
                "rating": "down",
                "corrected_capability": "report.generate",
                "comment": "vui lòng tổng hợp báo cáo tồn kho",
            }
        )
        assert eng.get_rules()[0].capability == "report.generate"
        # persisted
        eng2 = LearningEngine(rules_path=tmp_path / "rules.json")
        assert eng2.get_rules()[0].keyword

    async def test_run_cycle_report(self, tmp_path: Path) -> None:
        eng = LearningEngine(rules_path=tmp_path / "rules.json")
        report = await eng.run_cycle(
            [
                {"rating": "up"},
                {
                    "rating": "down",
                    "comment": "vui lòng tra cứu mail giúp",
                    "corrected_capability": "gmail.search",
                },
            ]
        )
        assert report["feedback_count"] == 2
        assert report["ratings"] == {"up": 1, "down": 1}
        assert report["rules_total"] == 1


class TestReflection:
    async def test_critique_with_mock_llm(self) -> None:
        eng = ReflectionEngine(llm=MockLLMProvider())
        result = await eng.critique("t-1", "knowledge.query", "what is X?", "X is ...")
        assert "score" in result
        # mock provider may not produce structured output — must not raise
