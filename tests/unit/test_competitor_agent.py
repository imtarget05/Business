"""Task 5 — Competitive Intelligence unit tests.

Covers:
* ``CompetitorAgent.collect`` parses ``web_search`` results into signals
  (mocked web_tools — fast, no network).
* ``CompetitorAgent.analyze`` groups by competitor, detects pricing shifts
  and pattern tags; degrades to heuristic summary when LLM is absent.
* ``CompetitorAgent.weekly_brief`` returns a short Markdown brief with the
  required sections (top movers / pricing shifts / summary / recommendations).
* Price extraction + direction + pattern-tag heuristics are deterministic.
* Capabilities ``competitor.brief`` / ``competitor.collect`` registered and the
  agent lives under ``Domain.COMPETITOR``.
* ``handle`` returns SUCCESS for both actions; rejects unknown actions.
* Registry + bootstrap resolve ``competitor.brief`` to the competitor agent.

All tests are fast and offline (web_search + LLM mocked/optional).
"""

from __future__ import annotations

import uuid as _uuid

from agents.competitor.agent import (
    CompetitorAgent,
    CompetitorSignal,
    _detect_price,
    _direction_for,
    _pattern_tags_for,
)
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentResponse, TaskRequest
from packages.llm.mock import MockLLMProvider


# A fake web_tools provider returning deterministic results (no network).
class _FakeWebTools:
    def __init__(self, results: list[dict]) -> None:
        self._results = results
        self.calls: list[tuple[str, int]] = []

    async def web_search(self, query: str, limit: int = 5) -> dict:
        self.calls.append((query, limit))
        return {"data": {"web": self._results}}

    async def web_extract(self, urls, char_limit: int = 5000) -> dict:  # pragma: no cover
        return {"results": []}


# Sample competitor config used by the agent's resolution logic.
_SAMPLE_COMPETITORS = [
    {"name": "DoiThuA", "aliases": ["doithua"], "keywords": ["DoiThuA"]},
    {"name": "DoiThuB", "aliases": ["doithub"], "keywords": ["DoiThuB"]},
]

_RAW_RESULTS = [
    {
        "title": "DoiThuA ra mắt gói mới giá 1.200.000 VND",
        "url": "https://doithua.com/tin-1",
        "snippet": "DoiThuA vừa ra mắt tính năng mới và tăng giá gói premium lên 1.200.000 VND.",
    },
    {
        "title": "DoiThuB giảm giá khuyến mãi 250k",
        "url": "https://doithub.com/khuyen-mai",
        "snippet": "DoiThuB đang giảm giá flash sale còn 250k cho gói cơ bản, đối tác mới.",
    },
    {
        "title": "Some unrelated news about weather",
        "url": "https://example.com/weather",
        "snippet": "Thời tiết hôm nay đẹp.",
    },
]


def _make_agent(llm=None, results=_RAW_RESULTS) -> CompetitorAgent:
    web = _FakeWebTools(results)
    agent = CompetitorAgent(llm=llm, web_tools=web)
    # Use the real placeholder competitors config (DoiThuA / DoiThuB aliases).
    agent._competitors_path = CompetitorAgent.__init__.__globals__["COMPETITORS_CONFIG_PATH"]
    return agent


# --------------------------------------------------------------------------- #
# collect — parse signals from mocked web_search
# --------------------------------------------------------------------------- #
async def test_collect_parses_signals() -> None:
    agent = _make_agent()
    # Two queries, each returns the 3 fake results -> 6 signals.
    signals = await agent.collect(["DoiThuA", "DoiThuB"])
    assert len(signals) == 2 * len(_RAW_RESULTS)
    assert all(isinstance(s, CompetitorSignal) for s in signals)
    # Competitor resolution from aliases/keywords.
    names = {s.competitor for s in signals}
    assert "DoiThuA" in names
    assert "DoiThuB" in names
    # Unrelated result falls back to host.
    assert any(s.competitor == "example.com" for s in signals)


async def test_collect_extracts_prices() -> None:
    agent = _make_agent()
    signals = await agent.collect(["prices"])
    priced = [s for s in signals if s.price_amount is not None]
    assert len(priced) == 2
    a = next(s for s in signals if s.competitor == "DoiThuA")
    assert a.price_amount == 1_200_000
    assert a.price_unit == "VND"
    assert a.price_direction == "up"
    b = next(s for s in signals if s.competitor == "DoiThuB")
    assert b.price_amount == 250_000
    assert b.price_direction == "down"


async def test_collect_called_with_queries() -> None:
    agent = _make_agent()
    await agent.collect(["q1", "q2", "q3"])
    assert agent._web.calls == [("q1", 5), ("q2", 5), ("q3", 5)]


# --------------------------------------------------------------------------- #
# analyze — grouping + pricing shifts + pattern tags
# --------------------------------------------------------------------------- #
async def test_analyze_no_llm_heuristic_fallback() -> None:
    agent = _make_agent(llm=None)
    signals = await agent.collect(["x"])
    analysis = await agent.analyze(signals)
    assert analysis.total_signals == len(signals)
    assert analysis.by_competitor.get("DoiThuA") == 1
    assert analysis.by_competitor.get("DoiThuB") == 1
    assert analysis.pricing_signals == 2
    assert len(analysis.price_shifts) == 2
    assert analysis.heuristics_only is True
    assert analysis.summary  # heuristic summary produced


async def test_analyze_pattern_tags() -> None:
    agent = _make_agent(llm=None)
    signals = await agent.collect(["x"])
    analysis = await agent.analyze(signals)
    # "ra mắt" / "launch" / "đối tác" should be captured as pattern tags.
    assert any(t in analysis.pattern_tags for t in ("ra mắt", "launch", "đối tác", "partner"))


async def test_analyze_uses_llm_when_available() -> None:
    llm = MockLLMProvider(scripted=["Tóm tắt LLM: đối thủ A tăng giá, B giảm giá."])
    agent = _make_agent(llm=llm)
    signals = await agent.collect(["x"])
    analysis = await agent.analyze(signals)
    assert analysis.heuristics_only is False
    assert "Tóm tắt LLM" in analysis.summary


async def test_analyze_llm_failure_falls_back() -> None:
    # MockLLMProvider raises on unscripted structured/generate only if needed;
    # here we make generate raise to prove graceful fallback.
    class _BoomLLM(MockLLMProvider):
        async def generate(self, *args, **kwargs):
            raise RuntimeError("llm down")

    agent = _make_agent(llm=_BoomLLM())
    signals = await agent.collect(["x"])
    analysis = await agent.analyze(signals)
    assert analysis.heuristics_only is True
    assert analysis.summary  # still has heuristic summary


# --------------------------------------------------------------------------- #
# weekly_brief — short Markdown with required sections
# --------------------------------------------------------------------------- #
async def test_weekly_brief_structure() -> None:
    agent = _make_agent(llm=MockLLMProvider(scripted=["Tóm tắt ngắn."]))
    brief = await agent.weekly_brief(org_id="00000000-0000-0000-0000-000000000001")
    assert isinstance(brief, str)
    assert "Top movers" in brief
    assert "Dịch chuyển giá" in brief
    assert "Tóm tắt" in brief
    assert "Đề xuất" in brief
    # Brief is short (well under 400 words of meaningful VN text).
    assert len(brief.split()) < 400


async def test_weekly_brief_per_competitor() -> None:
    agent = _make_agent(llm=None)
    brief = await agent.weekly_brief(competitor="DoiThuA")
    # Only DoiThuA signals should dominate / be present.
    assert "DoiThuA" in brief
    # When narrowed, brief should not reference the other competitor's name.
    assert "DoiThuB" not in brief


# --------------------------------------------------------------------------- #
# heuristic helpers
# --------------------------------------------------------------------------- #
def test_detect_price_variants() -> None:
    assert _detect_price("giá 1.200.000 VND") == (1_200_000, "VND")
    assert _detect_price("chỉ 250k thôi") == (250_000, "VND")
    assert _detect_price("19 USD") == (19.0, "USD")
    amt, unit = _detect_price("2 triệu")
    assert amt == 2_000_000 and unit == "VND"
    assert _detect_price("không có giá") == (None, None)


def test_direction_for() -> None:
    assert _direction_for("tăng giá gói premium") == "up"
    assert _direction_for("giảm giá khuyến mãi flash sale") == "down"
    assert _direction_for("ra mắt tính năng mới") is None


def test_pattern_tags_for() -> None:
    tags = _pattern_tags_for("đối thủ ra mắt tính năng mới và có đối tác")
    assert "ra mắt" in tags
    assert "đối tác" in tags


# --------------------------------------------------------------------------- #
# capability / domain / handle
# --------------------------------------------------------------------------- #
def test_agent_registers_competitor_capabilities() -> None:
    agent = CompetitorAgent(llm=MockLLMProvider())
    assert agent.descriptor.domain is Domain.COMPETITOR
    assert "competitor.brief" in agent.descriptor.capabilities
    assert "competitor.collect" in agent.descriptor.capabilities
    assert agent.descriptor.qualified_name == "competitor-v1"


async def test_handle_brief_success() -> None:
    agent = _make_agent(llm=MockLLMProvider(scripted=["Tóm tắt."]))
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.COMPETITOR,
            action="brief",
            payload={},
        )
    )
    assert isinstance(resp, AgentResponse)
    assert resp.status is AgentResponseStatus.SUCCESS, resp.error
    assert "brief" in resp.result
    assert isinstance(resp.result["brief"], str)


async def test_handle_collect_success() -> None:
    agent = _make_agent(llm=None)
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.COMPETITOR,
            action="collect",
            payload={"queries": ["DoiThuA"]},
        )
    )
    assert resp.status is AgentResponseStatus.SUCCESS
    assert resp.result["count"] >= 1
    assert resp.result["signals"]


async def test_handle_unknown_action_rejected() -> None:
    agent = CompetitorAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.COMPETITOR,
            action="spy",
            payload={},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED


# --------------------------------------------------------------------------- #
# registry + bootstrap wiring
# --------------------------------------------------------------------------- #
async def test_registry_resolves_competitor_brief() -> None:
    from packages.core.registry import InMemoryAgentRegistry

    registry = InMemoryAgentRegistry()
    registry.register(
        CompetitorAgent(llm=MockLLMProvider()).descriptor,
        CompetitorAgent(llm=MockLLMProvider()),
    )
    desc, handler = registry.get_by_capability("competitor.brief")
    assert desc.domain is Domain.COMPETITOR
    assert "competitor.brief" in desc.capabilities


async def test_bootstrap_registers_competitor_agent() -> None:
    from packages.core.bootstrap import build_container

    ctn = build_container()
    desc, handler = ctn.registry.get_by_capability("competitor.brief")
    assert isinstance(handler, CompetitorAgent)
    assert desc.domain is Domain.COMPETITOR
