"""Domain agent base contract (skeleton — no business logic in Phase 0)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.contracts.models import AgentDescriptor, AgentResponse, TaskRequest


@runtime_checkable
class DomainAgent(Protocol):
    """Every specialized agent implements this.

    The orchestrator only ever interacts via `descriptor` + `handle`, so new
    agents plug in without touching routing code.
    """

    descriptor: AgentDescriptor

    async def handle(self, request: TaskRequest) -> AgentResponse: ...
