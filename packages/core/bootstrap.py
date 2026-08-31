"""Composition root: wires registry + agents + LLM provider + orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from agents.advisory import create_advisory_agent
from agents.calendar import create_calendar_agent
from agents.competitor import create_competitor_agent
from agents.context import create_context_agent
from agents.gmail import create_gmail_agent
from agents.knowledge import create_knowledge_agent
from agents.ops_hub import create_ops_hub_agent
from agents.reporting import create_reporting_agent
from agents.research import create_research_agent
from agents.root_cause import create_root_cause_agent
from agents.sales import create_sales_agent
from agents.supply_chain import (
    create_inventory_monitor,
    create_supply_chain_agents,
    create_supply_chain_reporter,
)
from agents.support import create_support_agent
from agents.youtube import create_youtube_agent
from packages.config.settings import Settings, get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import AgentDescriptor
from packages.core.audit import AuditService
from packages.core.graph import GraphOrchestrator
from packages.core.knowledge_base import KnowledgeBase
from packages.core.learning import LearningEngine
from packages.core.orchestrator import Orchestrator
from packages.core.persistence import NoopTaskStore, TaskStore
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.reflection import ReflectionEngine
from packages.core.registry import InMemoryAgentRegistry
from packages.core.router import RouterAgent
from packages.database.session import get_session_factory
from packages.llm.factory import get_llm_provider


@dataclass
class AppContainer:
    settings: Settings
    registry: InMemoryAgentRegistry
    orchestrator: Orchestrator | GraphOrchestrator  # type: ignore[annotation]  # classic or graph path
    task_store: TaskStore = None
    policy: PolicyChecker = None
    audit: AuditService = None
    learning: LearningEngine = None
    reflection: ReflectionEngine = None
    kb: KnowledgeBase | None = None

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
    session_factory = get_session_factory(s)

    # Full-text Second Brain: offline, no embedding model required.
    kb = KnowledgeBase(session_factory)

    knowledge_agent = create_knowledge_agent(kb=kb, llm=llm)
    registry.register(knowledge_agent.descriptor, knowledge_agent)

    # AI Advisory Council (Task 3): expert personas as system-prompt overrides
    # over the shared LLM. Auto-detects persona from question text.
    advisory_agent = create_advisory_agent(llm=llm)
    registry.register(advisory_agent.descriptor, advisory_agent)

    # Email-to-Proposal Automation (Task 4): email -> proposal + PDF + follow-up.
    # Deterministic offline pipeline (reportlab); LLM optional, not required.
    sales_agent = create_sales_agent(llm=llm)
    registry.register(sales_agent.descriptor, sales_agent)

    # Competitive Intelligence (Task 5): COLLECT -> ANALYZE -> WEEKLY BRIEF.
    # Deterministic collection via web_search (no LLM crawl); analyze uses the
    # shared LLM for a light VN summary with heuristic fallback when unavailable.
    competitor_agent = create_competitor_agent(llm=llm)
    registry.register(competitor_agent.descriptor, competitor_agent)

    # Business Ops Hub (Task 2): aggregates Gmail unread + Calendar + tasks.
    # Sources are injected; gmail/calendar default sources call the registry's
    # existing agents, the task provider reads ops_tasks from settings (config/env).
    from agents.ops_hub.tasks_provider import build_task_provider

    ops_hub_agent = create_ops_hub_agent(
        task_provider=build_task_provider(s),
        llm=llm,
    )
    registry.register(ops_hub_agent.descriptor, ops_hub_agent)
    reporting_agent = create_reporting_agent(llm=llm)
    registry.register(reporting_agent.descriptor, reporting_agent)
    support_agent = create_support_agent(llm=llm)
    registry.register(support_agent.descriptor, support_agent)

        # Supply Chain agents (Phase SC): PO inbound parsing, classification, routing
    supply_chain_agents = create_supply_chain_agents(llm=llm, settings=s)
    for agent in supply_chain_agents.values():
        registry.register(agent.descriptor, agent)

        # Supply Chain Inventory Monitor (Phase SC): low/out/over stock alerts
    inventory_monitor = create_inventory_monitor(llm=llm, settings=s)
    inventory_descriptor = AgentDescriptor(
        name="inventory_monitor",
        domain=Domain.SUPPLY_CHAIN,
        version="1",
                description=(
            "Monitor inventory levels, generate alerts for low stock, "
            "out-of-stock, and overstock conditions."
        ),
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
                description=(
            "Generate supply chain reports and dashboards from PO "
            "processing, approval, and inventory data."
        ),
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

    # Additional agents: context, calendar, gmail, research, youtube
    for _agent in (
        create_context_agent(llm=llm),
        create_calendar_agent(llm=llm),
        create_gmail_agent(llm=llm),
        create_research_agent(llm=llm),
        create_youtube_agent(llm=llm),
    ):
        registry.register(_agent.descriptor, _agent)

    # Root Cause Agent (Phase 3) — evidence-first analysis over audit+metrics
    root_cause_agent = create_root_cause_agent(llm=llm)
    registry.register(root_cause_agent.descriptor, root_cause_agent)

    if s.langgraph_enabled:
        orchestrator = GraphOrchestrator(registry, llm, settings=s)
    else:
        router_agent = RouterAgent(llm=llm, registry=registry)
        orchestrator = Orchestrator(registry, llm, router=router_agent)

    # Centralized audit layer (ADR-011): classic orchestrator gets it injected;
    # graph path keeps parity via container.audit for node-level use.
    audit_service = AuditService(session_factory=session_factory)

    # Learning loop (ADR-010): learned rules feed RouterAgent before fallbacks.
    learning_engine = LearningEngine()
    reflection_engine = ReflectionEngine(llm=llm)

    if isinstance(orchestrator, Orchestrator):
        orchestrator.set_audit(audit_service)
        orchestrator.set_reflection(reflection_engine)

    if isinstance(orchestrator, Orchestrator) and orchestrator._router is not None:
        orchestrator._router.set_dynamic_rules(
            [(r.keyword, r.capability) for r in learning_engine.get_rules()]
        )

    return AppContainer(
        settings=s,
        registry=registry,
        orchestrator=orchestrator,
        task_store=task_store,
        policy=policy,
        audit=audit_service,
        learning=learning_engine,
        reflection=reflection_engine,
        kb=kb,
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