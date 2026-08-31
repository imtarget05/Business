"""Task 1 — Knowledge Agent answer loop (full-text, no embedding).

Covers:
- knowledge.query retrieves context and answers WITH citations;
- HARD criterion: below-threshold query returns "no relevant information
  found" WITHOUT calling the LLM;
- payload validation.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.knowledge.agent import NO_INFO_ANSWER, KnowledgeAgent
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.mock import MockLLMProvider


@pytest.fixture()
async def agent_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'k.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kb = KnowledgeBase(factory)
    await kb.init()

    doc = tmp_path / "policy.md"
    doc.write_text(
        "Our refunds policy: customers may request refunds within 14 days. "
        "Shipping takes 3-5 business days.",
        encoding="utf-8",
    )
    await kb.add_document(doc)

    llm = MockLLMProvider(
        scripted=[
            json.dumps({"answer": "Refunds are processed within 14 days.", "confidence": 0.9})
        ]
    )
    yield {"kb": kb, "llm": llm}
    await engine.dispose()


def _request(question: str, org: UUID | None = None) -> TaskRequest:
    return TaskRequest(
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": question},
        context=TaskContext(organization_id=org),
    )


async def test_query_answers_with_citations(agent_env) -> None:
    agent = KnowledgeAgent(kb=agent_env["kb"], llm=agent_env["llm"])
    resp = await agent.handle(_request("what is your refunds policy"))
    assert resp.status == AgentResponseStatus.SUCCESS
    assert "answer" in resp.result
    assert len(resp.citations) >= 1
    assert resp.result["answer"] == "Refunds are processed within 14 days."


async def test_query_below_threshold_returns_no_info_without_llm(agent_env) -> None:
    """HARD ACCEPTANCE CRITERION — no weak-context guessing."""
    llm = agent_env["llm"]
    agent = KnowledgeAgent(kb=agent_env["kb"], llm=llm)
    resp = await agent.handle(_request("zzz qqq xyzzy plugh quantum banana"))
    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.result["answer"] == NO_INFO_ANSWER
    assert resp.citations == []
    # LLM was never called.
    assert len(llm.calls) == 0


async def test_query_missing_question_rejected(agent_env) -> None:
    agent = KnowledgeAgent(kb=agent_env["kb"], llm=agent_env["llm"])
    resp = await agent.handle(_request(""))
    assert resp.status == AgentResponseStatus.REJECTED
