"""Agent Registry (STEP 0.4).

Phase 0 uses an in-memory registry seeded with the two MVP agents. The
interface is storage-agnostic so Phase 1 can back it with the `agents` table
without changing the orchestrator.
"""

from __future__ import annotations

from typing import Protocol

from packages.contracts.enums import AgentStatus, Domain
from packages.contracts.models import AgentDescriptor
from packages.core.errors import AgentNotFoundError, AgentUnavailableError
from packages.observability.logging import get_logger

logger = get_logger("registry")


class AgentRegistry(Protocol):
    def register(self, descriptor: AgentDescriptor, agent: object) -> None: ...

    def get_by_capability(self, capability: str) -> AgentDescriptor: ...

    def list_agents(self) -> list[AgentDescriptor]: ...


class InMemoryAgentRegistry:
    """Thread-light registry for local dev, tests and Phase 0 API."""

    def __init__(self) -> None:
        self._descriptors: dict[str, AgentDescriptor] = {}
        self._handlers: dict[str, object] = {}

    def register(self, descriptor: AgentDescriptor, handler: object) -> None:
        key = descriptor.qualified_name
        self._descriptors[key] = descriptor
        self._handlers[key] = handler
        logger.info("agent_registered", extra={"agent": key})

    def _lookup_key(self, name_or_qualified: str) -> str:
        if name_or_qualified in self._descriptors:
            return name_or_qualified
        matches = [k for k in self._descriptors if k.startswith(f"{name_or_qualified}-v")]
        if len(matches) == 1:
            return matches[0]
        raise AgentNotFoundError(
            f"Agent {name_or_qualified!r} not found",
            details={"query": name_or_qualified},
        )

    def get(self, name_or_qualified: str) -> tuple[AgentDescriptor, object]:
        key = self._lookup_key(name_or_qualified)
        return self._descriptors[key], self._handlers[key]

    def get_by_capability(self, capability: str) -> tuple[AgentDescriptor, object]:
        for key, descriptor in self._descriptors.items():
            if capability in descriptor.capabilities:
                if descriptor.status != AgentStatus.ACTIVE:
                    raise AgentUnavailableError(
                        f"Agent {key} handles {capability!r} but is "
                        f"{descriptor.status.value}",
                        details={"capability": capability},
                    )
                return descriptor, self._handlers[key]
        raise AgentNotFoundError(
            f"No active agent advertises capability {capability!r}",
            details={"capability": capability},
        )

    def discover_by_domain(self, domain: Domain) -> list[AgentDescriptor]:
        return [
            d for d in self._descriptors.values() if d.domain == domain
        ]

    def list_agents(self) -> list[AgentDescriptor]:
        return list(self._descriptors.values())
