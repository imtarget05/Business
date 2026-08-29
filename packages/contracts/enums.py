"""Enumerations shared across the platform."""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    """Business domains. Phase 0 ships two; new domains plug in without
    rewriting the orchestrator (registry-driven routing)."""

    KNOWLEDGE = "knowledge"
    SUPPORT = "support"
    OPERATIONS = "operations"
    REPORT = "report"
    SUPPLY_CHAIN = "supply_chain"
    CONTEXT = "context"
    CALENDAR = "calendar"
    GMAIL = "gmail"
    RESEARCH = "research"
    YOUTUBE = "youtube"
    OPS = "ops"
    ADVISORY = "advisory"  # AI Advisory Council (Task 3): personas as system-prompt overrides
    SALES = "sales"  # Email-to-Proposal Automation (Task 4): email -> proposal + PDF + follow-up
    COMPETITOR = "competitor"  # Competitive Intelligence (Task 5): COLLECT -> ANALYZE -> WEEKLY BRIEF


class AgentResponseStatus(StrEnum):
    """Terminal status of a single agent execution."""

    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    TIMEOUT = "timeout"


class AgentStatus(StrEnum):
    """Lifecycle status of a registered agent."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DEGRADED = "degraded"
    RETIRED = "retired"


class TaskStatus(StrEnum):
    """Task lifecycle states (see docs/architecture/agent-flow.md).

    Allowed transitions are enforced by `packages.contracts.state_machine`;
    arbitrary state changes are rejected.
    """

    PENDING = "pending"
    CLASSIFYING = "classifying"
    ROUTING = "routing"
    RUNNING = "running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "dead_lettered"




