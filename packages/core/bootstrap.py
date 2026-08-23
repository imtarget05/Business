"""Composition root: wires registry + agents + LLM provider + orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from agents.knowledge import create_knowledge_agent
from agents.support import create_support_agent
from packages.config.settings import Settings, get_settings
from packages.core.orchestrator import Orchestrator
from packages.core.persistence import NoopTaskStore, TaskStore
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.registry import InMemoryAgentRegistry
from packages.llm.factory import get_llm_provider


@dataclass
class AppContainer:
    settings: Settings
    registry: InMemoryAgentRegistry
    orchestrator: Orchestrator
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
    registry.register(create_knowledge_agent().descriptor, create_knowledge_agent())
    registry.register(create_support_agent().descriptor, create_support_agent())
    llm = get_llm_provider(s)
    return AppContainer(
        settings=s,
        registry=registry,
        orchestrator=Orchestrator(registry, llm),
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

