"""Composition root: wires registry + agents + LLM provider + orchestrator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Union

from agents.knowledge import create_knowledge_agent
from agents.reporting import create_reporting_agent
from agents.support import create_support_agent
from agents.supply_chain import (
    create_supply_chain_agents,
    create_supply_chain_reporter,
    create_inventory_monitor,
)
from packages.config.settings import Settings, get_settings
from packages.contracts.models import AgentDescriptor
from packages.contracts.enums import Domain
from packages.core.orchestrator import Orchestrator
from packages.core.graph import GraphOrchestrator
from packages.core.persistence import NoopTaskStore, TaskStore
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.registry import InMemoryAgentRegistry
from packages.core.router import RouterAgent
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

    # Supply Chain agents (Phase SC) — PurchaseOrderAgent for PO inbound parsing/classification/routing
    supply_chain_agents = create_supply_chain_agents(llm=llm, settings=s)
    for agent in supply_chain_agents.values():
        registry.register(agent.descriptor, agent)

    # Supply Chain Inventory Monitor (Phase SC) — inventory level monitoring and alerting
    inventory_monitor = create_inventory_monitor(llm=llm, settings=s)
    inventory_descriptor = AgentDescriptor(
        name="inventory_monitor",
        domain=Domain.SUPPLY_CHAIN,
        version="1",
        description="Monitor inventory levels, generate alerts for low stock, out-of-stock, and overstock conditions.",
        capabilities=frozenset(
            {
                "supply_chain.check_inventory",
                "supply_chain.get_alerts",
                "supply_chain.get_summary",
            }
        ),
        timeout_ms=15_000,
        max_retries=1,
    )
    registry.register(inventory_descriptor, inventory_monitor)

    # Supply Chain Reporter (Phase SC) — generate summary reports and dashboards
    supply_chain_reporter = create_supply_chain_reporter(llm=llm, settings=s)
    reporter_descriptor = AgentDescriptor(
        name="supply_chain_reporter",
        domain=Domain.SUPPLY_CHAIN,
        version="1",
        description="Generate daily/weekly/monthly supply chain reports and dashboards from PO processing, approval, and inventory data.",
        capabilities=frozenset(
            {
                "supply_chain.generate_report",
                "supply_chain.get_dashboard",
                "supply_chain.get_po_report",
                "supply_chain.get_approval_report",
                "supply_chain.get_inventory_report",
            }
        ),
        timeout_ms=30_000,
        max_retries=1,
    )
    registry.register(reporter_descriptor, supply_chain_reporter)

    if s.langgraph_enabled:
        orchestrator = GraphOrchestrator(registry, llm)
    else:
        router_agent = RouterAgent(llm=llm, registry=registry)
        orchestrator = Orchestrator(registry, llm, router=router_agent)

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