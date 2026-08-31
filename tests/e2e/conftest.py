# Shared fixtures for the tests/e2e suite (Feature 4 - true end-to-end tests).
#
# These fixtures wire a fully-offline container (Mock LLM + mock embedding + an
# on-disk SQLite database so the Knowledge Base and RAG cache work without
# Postgres) and a lightweight fake Telegram client that captures outbound
# messages. The suite reuses the real build_container composition root and the
# existing MockEmbeddingProvider / MockLLMProvider so the E2E path exercises the
# same code the production graph/API would run.

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.config.settings import (
    EmbeddingProviderKind,
    LLMProviderKind,
    Settings,
)
from packages.core.bootstrap import build_container
from packages.llm.mock import MockLLMProvider
from packages.llm.mock_embedding import MockEmbeddingProvider


class LenientMockLLM(MockLLMProvider):
    """Mock LLM that never raises on unscripted generate_structured calls."""

    async def generate_structured(self, prompt, schema, **kwargs):  # type: ignore[override]
        import json

        raw = self._next_raw()
        if isinstance(raw, dict):
            if hasattr(schema, "model_validate"):
                return schema.model_validate(raw)
            return raw
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if data is not None:
            if hasattr(schema, "model_validate"):
                return schema.model_validate(data)
            return data
        if hasattr(schema, "model_fields"):
            defaults: dict[str, Any] = {}
            for name, field in schema.model_fields.items():
                if field.is_required():
                    ann = field.annotation
                    if ann is float or ann is int:
                        defaults[name] = 0.6
                    else:
                        defaults[name] = "[mock] e2e generated answer"
                else:
                    defaults[name] = field.get_default()
            return schema.model_validate(defaults)
        return raw


class FakeTelegramBot:
    """Captures everything the bot would push to Telegram (no network)."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.chat_actions: list[str] = []
        self.replies: list[str] = []

    async def send_message(self, chat_id, text, parse_mode="Markdown", **kwargs):
        self.sent_messages.append(text)

    async def reply_text(self, text, parse_mode="Markdown", **kwargs):
        self.replies.append(text)

    async def send_chat_action(self, chat_id, action):
        self.chat_actions.append(action)


def format_support_reply(response: Any) -> str:
    """Turn an AgentResponse into a Vietnamese Telegram reply string."""
    knowledge = response.result.get("knowledge", {})
    answer = knowledge.get("answer") if isinstance(knowledge, dict) else None
    if not answer:
        answer = response.result.get("summary") or "(no answer)"
    lines = ["Ho tro khach hang:", str(answer)]
    if response.citations:
        lines.append("")
        lines.append("Nguon tham khao:")
        for c in response.citations:
            title = getattr(c, "title", None) or "source"
            lines.append("  - " + str(title))
    return "\n".join(lines)


@pytest.fixture
def e2e_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Offline container: Mock LLM + mock embedding + on-disk SQLite."""
    from packages.database import session as db_session

    db_session._engine = None  # type: ignore[attr-defined]
    db_session._session_factory = None  # type: ignore[attr-defined]

    db_file = tmp_path / "e2e.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    settings = Settings(
        llm_provider=LLMProviderKind.MOCK,
        embedding_provider=EmbeddingProviderKind.MOCK,
        embedding_dimensions=768,
        database_url=db_url,
        langgraph_enabled=False,
        persistence_enabled=False,
        learning_enabled=False,
    )

    # bootstrap.py imports get_llm_provider by name, so patching the module
    # attribute does not reach the composition root. Instead patch the method on
    # the MockLLMProvider class itself so every MockLLMProvider instance used by
    # the container (knowledge agent, orchestrator classify, reflection) becomes
    # lenient: unscripted structured calls fall back to a schema-derived default
    # instead of raising. This keeps the E2E path green offline.
    from packages.llm.mock import MockLLMProvider as _MockLLM

    monkeypatch.setattr(_MockLLM, "generate_structured", LenientMockLLM.generate_structured)

    container = build_container(settings)
    return container


@pytest.fixture
def telegram_stub() -> FakeTelegramBot:
    """A fake Telegram client that records outbound messages."""
    return FakeTelegramBot()


@pytest.fixture
def sqlite_kb(tmp_path: Path):
    """A KnowledgeBase backed by SQLite with an explicit mock embedding provider."""
    from packages.core.knowledge_base import KnowledgeBase

    db_file = tmp_path / "kb_e2e.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory: async_sessionmaker[Any] = async_sessionmaker(engine, expire_on_commit=False)
    kb = KnowledgeBase(factory, embedding_provider=MockEmbeddingProvider(dim=96))
    return kb
