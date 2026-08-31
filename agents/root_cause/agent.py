"""Root Cause Agent (Phase 3).

LLM analysis over AUDIT events + METRICS snapshots — the "why did it fail?"
capability. Depends on the audit layer (ADR-011) existing; never guesses
without evidence (evidence-first responses only).
"""

from __future__ import annotations

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.core.agent_base import DomainAgent
from packages.llm.base import LLMProvider
from packages.observability.logging import get_logger
from packages.observability.metrics import get_metrics

logger = get_logger("root_cause")

_SYSTEM = (
    "You are a root-cause analyst. You receive audit events and metrics for a "
    "failed task and must explain the most likely root cause. Cite the specific "
    "evidence lines you used. If evidence is insufficient, say so explicitly "
    "instead of guessing."
)


class RootCauseAgent(DomainAgent):
    def __init__(self, *, llm: LLMProvider, audit_events: list[dict] | None = None) -> None:
        self._llm = llm
        self._audit_events = audit_events or []
        self.descriptor = AgentDescriptor(
            name="root_cause",
            domain=Domain.OPS,
            version="1",
            description=(
                "Root-cause analysis over audit events and metrics (evidence-first, no guessing)."
            ),
            capabilities=frozenset({"ops.root_cause", "ops.get_metrics"}),
            timeout_ms=30_000,
            max_retries=1,
        )

    def set_audit_events(self, events: list[dict]) -> None:
        self._audit_events = events

    async def handle(self, request: TaskRequest) -> AgentResponse:
        capability = f"{request.domain.value}.{request.action}"
        if capability == "ops.get_metrics":
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"metrics": get_metrics().snapshot()},
            )

        evidence = [e for e in self._audit_events if str(e.get("task_id")) == str(request.task_id)]
        evidence = evidence or self._audit_events
        if not evidence:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.ESCALATED,
                error=ErrorDetail(
                    code="EVIDENCE_UNAVAILABLE",
                    message="No audit evidence for analysis; refusing to guess.",
                ),
            )

        lines = [
            f"- {e.get('created_at', '')} {e.get('event')}: {e.get('payload')}"
            for e in evidence[:30]
        ]
        evidence_text = "\n".join(lines)
        try:
            raw = await self._llm.generate(
                f"AUDIT EVIDENCE:\n{evidence_text}\n\nTASK: {request.payload}",
                system=_SYSTEM,
                temperature=0.0,
                max_tokens=512,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="LLM_ERROR", message=str(exc)),
            )

        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={
                "analysis": raw,
                "evidence_count": len(evidence),
                "evidence": evidence[:30],
            },
        )


def create_root_cause_agent(*, llm: LLMProvider) -> RootCauseAgent:
    return RootCauseAgent(llm=llm)
