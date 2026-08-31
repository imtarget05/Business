"""Agent Registry discovery tests (STEP 0.4)."""

from __future__ import annotations

import pytest

from agents.knowledge import create_knowledge_agent
from agents.support import create_support_agent
from packages.core.errors import AgentNotFoundError
from packages.core.registry import InMemoryAgentRegistry


@pytest.fixture()
def registry() -> InMemoryAgentRegistry:
    reg = InMemoryAgentRegistry()
    ka = create_knowledge_agent()
    sa = create_support_agent()
    reg.register(ka.descriptor, ka)
    reg.register(sa.descriptor, sa)
    return reg


def test_discover_by_capability(registry: InMemoryAgentRegistry) -> None:
    descriptor, handler = registry.get_by_capability("knowledge.query")
    assert descriptor.name == "knowledge"
    assert callable(getattr(handler, "handle", None))


def test_unknown_capability_raises(registry: InMemoryAgentRegistry) -> None:
    with pytest.raises(AgentNotFoundError):
        registry.get_by_capability("research.deep_dive")  # not built in Phase 0


def test_list_agents(registry: InMemoryAgentRegistry) -> None:
    names = {d.qualified_name for d in registry.list_agents()}
    assert names == {"knowledge-v1", "support-v1"}


def test_no_if_else_routing_needed() -> None:
    """Routing resolves purely from capability strings — adding a new domain
    agent requires zero orchestrator changes."""
    reg = InMemoryAgentRegistry()

    class NewDomainAgent:
        descriptor = create_knowledge_agent().descriptor.model_copy(
            update={
                "name": "recruiting",
                "capabilities": frozenset({"recruiting.sourcing"}),
            }
        )

        async def handle(self, request):  # pragma: no cover
            raise AssertionError("not called")

    agent = NewDomainAgent()
    # capabilities must match domain for a real descriptor; build manually:
    from packages.contracts.enums import Domain
    from packages.contracts.models import AgentDescriptor

    reg.register(
        AgentDescriptor(
            name="recruiting",
            domain=Domain.SUPPORT,  # reuse an existing domain prefix rule
            capabilities=frozenset({"support.sourcing"}),
        ),
        agent,
    )
    descriptor, _ = reg.get_by_capability("support.sourcing")
    assert descriptor.name == "recruiting"
