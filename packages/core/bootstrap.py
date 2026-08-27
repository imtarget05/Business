"""Composition root: wires registry + agents + LLM provider + orchestrator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Union

from agents.knowledge import create_knowledge_agent
from agents.reporting import create_reporting_agent
from agents.support import create_support_agent
from packages.config.settings import Settings, get_settings
from packages.core.orchestrator import Orchestrator
from packages.core.graph import GraphOrchestrator
from packages.core.persistence import NoopTaskStore, TaskStore
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.registry import InMemoryAgentRegistry
from packages.database.repositories.documents import KnowledgeRepository
from packages.database.session import get_session_factory
from packages.llm.factory import get_embedding_provider, get_llm_provider


@dataclass
class AppContainer:
    settings: Settings
    registry: InMemoryAgentRegistry
    orchestrator: Union[Orchestrator, GraphOrchestrator]  # type: ignore[annotation]  # classic or graph path
    task_store: TaskStore = None
    policy: PolicyChecker = None

    def __post_init__(self) -> None:
        if self.task_store is None:
            self.task_store = NoopTaskStore()
        if self.policy is None:
            self.policy = AllowAllPolicy()


def build_container(
    settings: Settings | None = None,
    *,
    task_store: TaskStore | None = None,
    policy: PolicyChecker | None = None,
) -> AppContainer:
    s = settings or get_settings()
    registry = InMemoryAgentRegistry()
    llm = get_llm_provider(s)
    embeddings = get_embedding_provider(s)
    session_factory = get_session_factory(s)

    @asynccontextmanager
    async def _knowledge_repo() -> AsyncIterator[KnowledgeRepository]:
        async with session_factory() as session:
            yield KnowledgeRepository(session)

    knowledge_agent = create_knowledge_agent(
        repository=None,  # resolved per-request (async session); see handle()
        llm=llm,
        embeddings=embeddings,
        similarity_threshold=s.knowledge_similarity_threshold,
        repo_factory=_knowledge_repo,
    )
    registry.register(knowledge_agent.descriptor, knowledge_agent)
    reporting_agent = create_reporting_agent(llm=llm)
    registry.register(reporting_agent.descriptor, reporting_agent)
    support_agent = create_support_agent(llm=llm)
    registry.register(support_agent.descriptor, support_agent)

    if s.langgraph_enabled:
        orchestrator = GraphOrchestrator(registry, llm)
    else:
        orchestrator = Orchestrator(registry, llm)

    return AppContainer(
        settings=s,
        registry=registry,
        orchestrator=orchestrator,
        task_store=task_store,
        policy=policy,
    )


_container: AppContainer | None = None


def get_container() -> AppContainer:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def set_container(container: AppContainer | None) -> None:
    """Used by tests to inject a fresh container."""
    global _container
    _container = container