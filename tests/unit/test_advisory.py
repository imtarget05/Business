"""Task 3 — AI Advisory Council unit tests.

Covers:
* ``select_persona`` deterministic keyword auto-detection (per persona).
* ``PERSONAS`` dict shape (3 experts, non-empty system prompts).
* ``AdvisoryAgent.handle`` applies the correct persona system prompt to the
  shared LLM (no separate model) for an explicit persona and for auto-detect.
* Auto-detect routes a free-text question to the correct persona.
* Capability ``advisory.ask`` registration (domain ``advisory``).

All tests are fast and use a :class:`MockLLMProvider` (no network/model).
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from agents.advisory.agent import AdvisoryAgent, create_advisory_agent
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentResponse, TaskRequest
from packages.core.personas import PERSONA_LABELS, PERSONAS, select_persona
from packages.llm.mock import MockLLMProvider


class RecordingLLM(MockLLMProvider):
    """Mock LLM that also records the ``system`` prompt passed to generate_structured.

    Used to assert the correct persona system prompt is applied to the shared
    LLM (personas are system-prompt overrides, no separate model).
    """

    def __init__(self, *, answer: str = "x", confidence: float = 0.6) -> None:
        super().__init__(scripted=[{"answer": answer, "confidence": confidence}])
        self.systems: list[str] = []

    async def generate_structured(self, prompt, schema, *, system=None, **kwargs):
        self.systems.append(system)
        return await super().generate_structured(prompt, schema, system=system, **kwargs)


# ---------------------------------------------------------------------------
# personas.select_persona — keyword auto-detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Làm sao để tăng trưởng doanh thu?", "hormozi"),
        ("Chiến lược pricing cho gói SaaS này?", "hormozi"),
        ("Should I invest in dividend stocks?", "buffett"),
        ("Warren Buffett nghĩ gì về cổ phiếu?", "buffett"),
        ("Có nên mua cổ phiếu chia cổ tức không?", "buffett"),
        ("Marketing trên TikTok hiệu quả không?", "garyvee"),
        ("Xây dựng thương hiệu cá nhân thế nào?", "garyvee"),
        ("Quản lý tài chính cá nhân ra sao?", "garyvee"),
        ("bạn khỏe không?", None),
        ("", None),
    ],
)
def test_select_persona(text: str, expected: str | None) -> None:
    assert select_persona(text) == expected


def test_select_persona_case_insensitive() -> None:
    assert select_persona("BUFFETT và đầu tư") == "buffett"
    assert select_persona("MARKETING content") == "garyvee"


def test_select_persona_priority_hormozi_first() -> None:
    # Ensure disjoint sets don't cause surprises; a pure-strategy query maps to hormozi.
    assert select_persona("business model và tăng trưởng") == "hormozi"


# ---------------------------------------------------------------------------
# personas dict shape
# ---------------------------------------------------------------------------
def test_personas_dict_shape() -> None:
    assert set(PERSONAS) == {"hormozi", "buffett", "garyvee"}
    for key, prompt in PERSONAS.items():
        assert isinstance(prompt, str) and prompt.strip()
        assert key in PERSONA_LABELS


# ---------------------------------------------------------------------------
# Agent construction / capability
# ---------------------------------------------------------------------------
def test_agent_registers_advisory_ask_capability() -> None:
    agent = AdvisoryAgent(llm=MockLLMProvider())
    assert agent.descriptor.domain is Domain.ADVISORY
    assert "advisory.ask" in agent.descriptor.capabilities
    assert agent.descriptor.qualified_name == "advisory-v1"


def test_factory_builds_agent() -> None:
    agent = create_advisory_agent(llm=MockLLMProvider())
    assert isinstance(agent, AdvisoryAgent)


# ---------------------------------------------------------------------------
# handle(): persona system prompt applied to the shared LLM
# ---------------------------------------------------------------------------
async def test_explicit_persona_prompt_is_applied() -> None:
    for persona in ("hormozi", "buffett", "garyvee"):
        llm = RecordingLLM(answer="x", confidence=0.7)
        agent = AdvisoryAgent(llm=llm)
        resp = await agent.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.ADVISORY,
                action="ask",
                payload={"question": "Câu hỏi?", "persona": persona},
            )
        )
        assert resp.status is AgentResponseStatus.SUCCESS, resp.error
        # The persona's system prompt must be the one passed to the LLM.
        assert llm.systems[-1] == PERSONAS[persona]
        assert resp.result["persona"] == persona
        assert resp.result["persona_label"] == PERSONA_LABELS[persona]
        assert resp.metadata["auto_detected"] is False


async def test_autodetect_routes_to_correct_persona() -> None:
    cases = [
        ("Có nên mua cổ phiếu chia cổ tức không?", "buffett"),
        ("Chiến lược tăng trưởng cho startup?", "hormozi"),
        ("Marketing TikTok hiệu quả không?", "garyvee"),
    ]
    for question, expected in cases:
        llm = RecordingLLM(answer="y", confidence=0.6)
        agent = AdvisoryAgent(llm=llm)
        resp = await agent.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.ADVISORY,
                action="ask",
                payload={"question": question},
            )
        )
        assert resp.status is AgentResponseStatus.SUCCESS, resp.error
        assert resp.result["persona"] == expected
        assert llm.systems[-1] == PERSONAS[expected]
        assert resp.metadata["auto_detected"] is True


async def test_no_persona_and_no_keyword_defaults_hormozi() -> None:
    llm = MockLLMProvider(scripted=[{"answer": "z", "confidence": 0.5}])
    agent = AdvisoryAgent(llm=llm)
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.ADVISORY,
            action="ask",
            payload={"question": "bạn khỏe không?"},
        )
    )
    assert resp.status is AgentResponseStatus.SUCCESS
    assert resp.result["persona"] == "hormozi"


async def test_missing_question_is_rejected() -> None:
    agent = AdvisoryAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.ADVISORY,
            action="ask",
            payload={},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED
    assert resp.error is not None


async def test_unknown_action_is_rejected() -> None:
    agent = AdvisoryAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.ADVISORY,
            action="summon",
            payload={"question": "x"},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED


async def test_missing_llm_is_rejected() -> None:
    agent = AdvisoryAgent(llm=None)
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.ADVISORY,
            action="ask",
            payload={"question": "x", "persona": "buffett"},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED
    assert resp.error is not None


# ---------------------------------------------------------------------------
# ask() convenience entry point
# ---------------------------------------------------------------------------
async def test_ask_convenience_method() -> None:
    llm = RecordingLLM(answer="ok", confidence=0.8)
    agent = AdvisoryAgent(llm=llm)
    resp = await agent.ask("Should I invest in dividend stocks?", persona="buffett")
    assert isinstance(resp, AgentResponse)
    assert resp.status is AgentResponseStatus.SUCCESS
    assert resp.result["persona"] == "buffett"
    assert llm.systems[-1] == PERSONAS["buffett"]


# ---------------------------------------------------------------------------
# Registry wiring (capability resolves to the advisory agent)
# ---------------------------------------------------------------------------
async def test_registry_resolves_advisory_ask() -> None:
    from packages.core.registry import InMemoryAgentRegistry

    registry = InMemoryAgentRegistry()
    registry.register(
        AdvisoryAgent(llm=MockLLMProvider()).descriptor,
        AdvisoryAgent(llm=MockLLMProvider()),
    )
    desc, handler = registry.get_by_capability("advisory.ask")
    assert isinstance(handler, AdvisoryAgent)
    assert desc.domain is Domain.ADVISORY
    assert "advisory.ask" in desc.capabilities
