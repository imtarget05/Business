"""Phase 2 Task 2.4 — Knowledge Agent answer loop (TDD).

Covers:
- knowledge.query retrieves context and answers WITH citations;
- HARD criterion: below-threshold query returns "no relevant information
  found" WITHOUT calling the LLM;
- payload validation unchanged from Phase 0.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.knowledge.agent import KnowledgeAgent
from agents.knowledge.ingest import IngestionService
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.database import models
from packages.database.base import Base
from packages.database.repositories.documents import KnowledgeRepository
from packages.llm.base import EmbeddingProvider
from packages.llm.mock import MockLLMProvider


class TopicEmbedding(EmbeddingProvider):
    """Deterministic topic-vector embeddings for tests (768-dim)."""

    TOPICS = {
        "refund": [1.0, 0.0],
        "policy": [0.6, 0.0],
        "shipping": [0.0, 1.0],
    }

    @property
    def name(self) -> str:
        return "topic_embedding"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0, 0.0]
            matched = False
            for topic, vec in self.TOPICS.items():
                if topic in t.lower():
                    v = [a + b for a, b in zip(v, vec, strict=False)]
                    matched = True
            if not matched:
                # Orthogonal noise direction — far from every topic.
                v = [0.0, 1.0]
            norm = sum(x * x for x in v) ** 0.5
            v = [x / norm for x in v]
            out.append(v + [0.0] * 766)
        return out

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def agent_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'k.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                models.Organization.__table__,
                models.Document.__table__,
                models.DocumentChunk.__table__,
            ],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    repo = KnowledgeRepository(session)
    embeddings = TopicEmbedding()
    llm = MockLLMProvider(
        scripted=[
            json.dumps({"answer": "Refunds are processed within 14 days.", "confidence": 0.9})
        ]
    )
    service = IngestionService(repo, embeddings)
    org_id = uuid4()
    await service.ingest(
        organization_id=org_id,
        title="Policy",
        content="Our refunds policy: customers may request refunds within 14 days. "
        "Shipping takes 3-5 business days.",
        source_type="text",
    )
    yield {"repo": repo, "llm": llm, "embeddings": embeddings, "session": session, "org_id": org_id}
    await session.close()
    await engine.dispose()


def _request(question: str, org: UUID | None = None) -> TaskRequest:
    return TaskRequest(
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": question},
        context=TaskContext(organization_id=org),
    )


async def test_query_answers_with_citations(agent_env) -> None:
    agent = KnowledgeAgent(
        repository=agent_env["repo"], llm=agent_env["llm"], embeddings=TopicEmbedding()
    )
    resp = await agent.handle(_request("what is your refunds policy", agent_env["org_id"]))
    assert resp.status == AgentResponseStatus.SUCCESS
    assert "answer" in resp.result
    assert len(resp.citations) >= 1
    assert resp.citations[0].source_id != "placeholder-doc"


async def test_query_below_threshold_returns_no_info_without_llm(agent_env) -> None:
    """HARD ACCEPTANCE CRITERION — no weak-context guessing."""
    llm = agent_env["llm"]
    agent = KnowledgeAgent(repository=agent_env["repo"], llm=llm, embeddings=TopicEmbedding())
    resp = await agent.handle(_request("zzz qqq xyzzy plugh quantum banana", agent_env["org_id"]))
    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.result["answer"] == "no relevant information found"
    assert resp.citations == []
    # LLM was never called.
    assert len(llm.calls) == 0


async def test_query_missing_question_rejected(agent_env) -> None:
    agent = KnowledgeAgent(
        repository=agent_env["repo"], llm=agent_env["llm"], embeddings=TopicEmbedding()
    )
    resp = await agent.handle(_request(""))
    assert resp.status == AgentResponseStatus.REJECTED
