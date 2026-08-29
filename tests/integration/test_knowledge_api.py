"""Task 1 — /v1/knowledge/* API routes (full-text Second Brain).

Uses a minimal app containing only the knowledge router. The global container
is replaced with an in-memory fake wired to an SQLite KnowledgeBase and a
scripted MockLLM, so the test runs fully offline with no network.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import packages.core.bootstrap as bootstrap_mod
from agents.knowledge.agent import KnowledgeAgent
from packages.contracts.enums import Domain
from packages.contracts.models import AgentDescriptor
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.mock import MockLLMProvider


class _FakeRegistry:
    def __init__(self, handler: KnowledgeAgent) -> None:
        self._handler = handler
        self._descriptor = AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            version="1",
            capabilities=frozenset({"knowledge.query"}),
        )

    def get_by_capability(self, capability: str):
        return self._descriptor, self._handler


class _FakeContainer:
    def __init__(self, kb: KnowledgeBase, llm: MockLLMProvider) -> None:
        self.kb = kb
        self.llm = llm
        self.registry = _FakeRegistry(KnowledgeAgent(kb=kb, llm=llm, top_k=3))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'kb.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kb = KnowledgeBase(factory)
    asyncio.run(kb.init())

    doc = tmp_path / "policy.md"
    doc.write_text(
        "Our refunds policy: customers may request refunds within 14 days.",
        encoding="utf-8",
    )
    asyncio.run(kb.add_document(doc))

    llm = MockLLMProvider(
        scripted=[{"answer": "Refunds within 14 days.", "confidence": 0.9}]
    )
    container = _FakeContainer(kb, llm)
    monkeypatch.setattr(bootstrap_mod, "get_container", lambda: container)

    import apps.api.routes.knowledge as kb_routes

    async def _noop_session():
        yield None

    monkeypatch.setattr(kb_routes, "get_session", _noop_session)

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "faq.md").write_text("Shipping takes 3-5 business days.", encoding="utf-8")
    monkeypatch.setattr(kb_routes, "KB_DIR", kb_dir)

    app = FastAPI()
    app.include_router(kb_routes.router)
    with TestClient(app) as c:
        yield c
    asyncio.run(engine.dispose())


def test_index_endpoint(client) -> None:
    resp = client.post("/v1/knowledge/index")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["indexed"] >= 1
    assert "source" in data


def test_query_endpoint_returns_answer(client) -> None:
    resp = client.post("/v1/knowledge/query", json={"question": "refund policy"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"]
    assert isinstance(body["citations"], list)
    assert body["refused_to_answer"] is False


def test_query_endpoint_empty_question(client) -> None:
    resp = client.post("/v1/knowledge/query", json={"question": ""})
    assert resp.status_code == 422
