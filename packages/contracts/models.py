"""Typed Agent Contract (STEP 0.3) and Agent Registry contract (STEP 0.4).

All cross-boundary payloads are typed Pydantic models — `dict[str, Any]` is
only used inside opaque payload bags (`payload`, `result`, `metadata`), never
for the contract envelopes themselves.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from packages.contracts.enums import (
    AgentResponseStatus,
    AgentStatus,
    Domain,
)

CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*$")


# ---------------------------------------------------------------------------
# STEP 0.3 — Task / Agent contract
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A reference to a source backing an agent claim (used by knowledge flows)."""

    source_id: str
    title: str
    uri: str | None = None
    snippet: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class TaskContext(BaseModel):
    """Who/where the task originates from."""

    user_id: UUID | None = None
    organization_id: UUID | None = None
    channel: str = "api"  # api | n8n | dashboard | webhook
    locale: str = "en"
    trace_id: str | None = None


class TaskRequest(BaseModel):
    """Canonical request envelope sent to the orchestrator."""

    task_id: UUID = Field(default_factory=uuid4)
    domain: Domain
    action: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    context: TaskContext = Field(default_factory=TaskContext)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Canonical response envelope returned by a specialized agent."""

    task_id: UUID
    agent: str  # "<name>-v<version>", e.g. "knowledge-v1"
    status: AgentResponseStatus
    result: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    error: ErrorDetail | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_error_presence(self) -> AgentResponse:
        if self.status != AgentResponseStatus.SUCCESS and self.error is None:
            raise ValueError(
                f"non-success responses ({self.status.value}) must include `error`"
            )
        if self.status == AgentResponseStatus.SUCCESS and self.error is not None:
            raise ValueError("success responses must not include `error`")
        return self


# ---------------------------------------------------------------------------
# STEP 0.4 — Agent Registry contract
# ---------------------------------------------------------------------------


class AgentDescriptor(BaseModel):
    """Registry entry describing one deployable agent version.

    The orchestrator discovers agents from the registry by capability — it
    must never hard-code `if domain == ...` routing.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    domain: Domain
    version: str = Field(default="1", pattern=r"^\d+(\.\d+)*$")
    description: str = ""
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    status: AgentStatus = AgentStatus.ACTIVE
    timeout_ms: int = Field(default=30_000, gt=0)
    max_retries: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> AgentDescriptor:
        expected_prefix = f"{self.domain.value}."
        for cap in self.capabilities:
            if not CAPABILITY_PATTERN.match(cap):
                raise ValueError(f"invalid capability format: {cap!r}")
            if not cap.startswith(expected_prefix):
                raise ValueError(
                    f"capability {cap!r} must start with {expected_prefix!r} "
                    f"to match agent domain {self.domain.value!r}"
                )
        return self

    @property
    def qualified_name(self) -> str:
        return f"{self.name}-v{self.version}"


__all__ = [
    "AgentDescriptor",
    "AgentResponse",
    "Citation",
    "ErrorDetail",
    "TaskContext",
    "TaskRequest",
]
