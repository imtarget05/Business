"""Authorization policy boundary (Phase 1 Item 2).

The orchestrator checks a ``PolicyChecker`` after ROUTING and before RUNNING so
the access decision is registry-driven (``capability = domain.action``), never
a ``if domain == ...`` chain. Swapping the checker requires no route changes.

MVP ships ``AllowAllPolicy`` (any authenticated caller may perform any
capability). Role-based policies (e.g. ``support.escalate`` requiring an admin
role) implement the same Protocol later without touching orchestration core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.contracts.models import TaskContext


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""


class PolicyChecker(Protocol):
    """Decides whether a capability may run for a given task context."""

    async def check(self, *, capability: str, context: TaskContext) -> PolicyDecision: ...


class AllowAllPolicy:
    """Default MVP: allow all capabilities once the caller is authenticated."""

    async def check(self, *, capability: str, context: TaskContext) -> PolicyDecision:
        return PolicyDecision(allowed=True)


__all__ = ["AllowAllPolicy", "PolicyChecker", "PolicyDecision"]
