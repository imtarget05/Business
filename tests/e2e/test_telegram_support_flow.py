# -*- coding: utf-8 -*-
"""E2E: Telegram (Vietnamese) support query -> Orchestrator -> Knowledge handoff
-> Telegram reply, fully offline (Mock LLM + mock embedding + SQLite KB).

This is the headline end-to-end path: a Telegram user message is classified,
routed to the support agent, which hands off to the knowledge agent (hybrid
full-text + vector retrieval over the Knowledge Base), and the resulting
AgentResponse is formatted into the outbound Telegram reply.

These tests go beyond structure/status checks: they SEED a real Vietnamese
document into the KB and then assert the returned answer/citations actually
derive from that document, so a dead retrieval path (vector cast, deadlock, or
a too-aggressive min-similarity threshold) makes the test fail instead of
passing vacuously.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agents.knowledge.agent import NO_INFO_ANSWER
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import Citation, TaskContext, TaskRequest
from packages.telegram.nlp import classify_vietnamese_intent

from tests.e2e.conftest import FakeTelegramBot, format_support_reply


VIETNAMESE_PASSWORD_DOC = """
Neu ban quen mat khau, hay dat lai mat khau qua lien ket Quen mat khau tren
trang dang nhap. He thong se gui ma xac nhan ve email da dang ky cua ban.
Sau khi nhap ma xac nhan, ban co the tao mat khau moi va dang nhap lai.
"""

VIETNAMESE_QUERY = "Toi can ho tro, toi quen mat khau lam sao de dat lai?"

SEED_PHRASE = "ma xac nhan"


@pytest.mark.e2e
async def test_telegram_support_knowledge_end_to_end(e2e_container, telegram_stub):
    # 1) Telegram NLP step: the inbound Vietnamese message is classified.
    intent = classify_vietnamese_intent(VIETNAMESE_QUERY)
    assert intent == "support.triage"

    # 2) Seed the Knowledge Base with a Vietnamese article (offline, mock embed).
    with tempfile.TemporaryDirectory() as d:
        doc_path = Path(d) / "password_reset.md"
        doc_path.write_text(VIETNAMESE_PASSWORD_DOC, encoding="utf-8")
        await e2e_container.kb.add_document(doc_path)

        # 3) Build the support TaskRequest that triggers the knowledge handoff.
        req = TaskRequest(
            domain=Domain.SUPPORT,
            action="triage",
            payload={
                "subject": "Quen mat khau",
                "body": VIETNAMESE_QUERY,
                "needs_knowledge": True,
                "question": VIETNAMESE_QUERY,
            },
            context=TaskContext(channel="telegram", locale="vi"),
        )

        # 4) Drive the full path through the orchestrator (incl. knowledge handoff).
        resp = await e2e_container.orchestrator.execute(req)

    # 5) The final response is a success and carries the knowledge handoff result.
    assert resp.status == AgentResponseStatus.SUCCESS
    assert "knowledge" in resp.result
    knowledge = resp.result["knowledge"]
    answer = knowledge.get("answer")

    # Retrieval must have produced grounded context. If hybrid retrieval is dead
    # the knowledge agent returns NO_INFO_ANSWER (the hard "never answer without
    # verified context" rule); this assertion catches that regression.
    assert answer, "knowledge answer is empty"
    assert answer != NO_INFO_ANSWER, (
        "knowledge agent returned NO_INFO_ANSWER: hybrid retrieval found nothing "
        "for the seeded document (vector path dead / min-similarity too high?)"
    )

    # The citations must actually derive from the seeded document: at least one
    # Citation.snippet contains seeded text.
    assert isinstance(resp.citations, list)
    assert resp.citations, "knowledge response must include citations"
    assert any(
        isinstance(c, Citation)
        and c.snippet is not None
        and SEED_PHRASE in c.snippet.lower()
        for c in resp.citations
    ), "no citation references the seeded document"

    # 6) Simulate the Telegram bot formatting the response into an outbound reply.
    reply_text = format_support_reply(resp)
    assert isinstance(telegram_stub, FakeTelegramBot)
    await telegram_stub.send_message(chat_id=123456, text=reply_text)

    # 7) The captured Telegram reply is non-empty and contains expected content.
    assert telegram_stub.sent_messages, "no Telegram message was sent"
    final_message = telegram_stub.sent_messages[-1]
    assert final_message.strip()
    assert "mat khau" in final_message.lower() or "ho tro" in final_message.lower()


@pytest.mark.e2e
async def test_knowledge_retrieval_returns_seeded_document(e2e_container):
    """Guard specifically for the retrieval layer: both the full-text (FTS) and
    the semantic (vector) retrievers must surface the seeded Vietnamese document
    for the query. This catches the vector-cast / deadlock / min-similarity
    regressions that a pure status check would miss."""
    with tempfile.TemporaryDirectory() as d:
        doc_path = Path(d) / "password_reset.md"
        doc_path.write_text(VIETNAMESE_PASSWORD_DOC, encoding="utf-8")
        await e2e_container.kb.add_document(doc_path)

        # Full-text retrieval path.
        fts_hits = await e2e_container.kb.query(VIETNAMESE_QUERY, k=3)
        assert fts_hits, "full-text retrieval returned nothing for the seeded doc"
        assert any(SEED_PHRASE in hit.lower() for hit in fts_hits), (
            "full-text retrieval did not return the seeded document"
        )

        # Semantic (vector) retrieval path.
        vec_hits = await e2e_container.kb.query_vector(VIETNAMESE_QUERY, top_k=3)
        assert vec_hits, "vector retrieval returned nothing for the seeded doc"
        assert any(SEED_PHRASE in hit.lower() for hit in vec_hits), (
            "vector retrieval did not return the seeded document"
        )


@pytest.mark.e2e
async def test_telegram_support_empty_kb_returns_no_info(e2e_container, telegram_stub):
    """With no documents seeded, the support->knowledge handoff must NOT invent
    an answer. The hard rule is: never answer without verified context, so the
    agent returns NO_INFO_ANSWER with no citations."""
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "Khong co du lieu",
            "body": "Toi muon hoi ve van de khong co trong co so du lieu",
            "needs_knowledge": True,
            "question": "Toi muon hoi ve van de khong co trong co so du lieu",
        },
        context=TaskContext(channel="telegram", locale="vi"),
    )
    resp = await e2e_container.orchestrator.execute(req)
    assert resp.status == AgentResponseStatus.SUCCESS
    assert "knowledge" in resp.result
    assert resp.result["knowledge"].get("answer") == NO_INFO_ANSWER, (
        "empty-KB query must return NO_INFO_ANSWER (never answer without "
        "verified context); instead got a non-empty/fabricated answer"
    )
    assert resp.citations == [], "empty-KB query must not produce citations"
