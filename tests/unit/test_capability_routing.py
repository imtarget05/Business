"""Unit tests for capability-score routing (ADR-012)."""

from __future__ import annotations

from packages.core.router import RouterAgent, score_candidates
from packages.llm.mock import MockLLMProvider


class _Descriptor:
    def __init__(self, name: str, domain: str, capabilities: set[str]) -> None:
        self.qualified_name = f"{name}-v1"
        self.domain = type("D", (), {"value": domain})()
        self.capabilities = capabilities


class _Registry:
    def __init__(self, descriptors: list) -> None:
        self._descriptors = descriptors

    def list_agents(self) -> list:
        return self._descriptors


class TestScoreCandidates:
    def test_supply_chain_query_scores_inventory(self) -> None:
        reg = _Registry(
            [
                _Descriptor("inventory", "supply_chain", {"supply_chain.check_inventory"}),
                _Descriptor("knowledge", "knowledge", {"knowledge.query"}),
            ]
        )
        scored = score_candidates("Kiểm tra tồn kho của nhà kho A", reg)
        assert scored
        assert scored[0][0] == "inventory-v1"

    def test_no_match_returns_empty(self) -> None:
        reg = _Registry([_Descriptor("knowledge", "knowledge", {"knowledge.query"})])
        assert score_candidates("asdfghjkl", reg) == []

    def test_none_registry(self) -> None:
        assert score_candidates("anything", None) == []

    def test_router_agent_candidates_method(self) -> None:
        reg = _Registry([_Descriptor("research", "research", {"research.web_search"})])
        router = RouterAgent(llm=MockLLMProvider(), registry=reg)
        scored = router.candidates("tìm kiếm nghiên cứu về AI")
        assert scored and scored[0][0] == "research-v1"
