"""Support Agent skeleton (real triage/drafting arrives in Phase 3)."""

from __future__ import annotations

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)

SUPPORTED_ACTIONS = {"triage", "draft_reply"}


class SupportAgent:
    def __init__(self, descriptor: AgentDescriptor | None = None) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="support",
            domain=Domain.SUPPORT,
            version="1",
            description="Triage inbound support requests and draft replies "
            "(Phase 0 skeleton: rule-free echo implementation).",
            capabilities=frozenset({"support.triage", "support.draft_reply"}),
        )

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"unsupported action {request.action!r} for support-v1",
                ),
            )
        subject = str(request.payload.get("subject", "")).strip()
        if not subject:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.ESCALATED,
                error=ErrorDetail(
                    code="ROUTING_ERROR",
                    message="payload.subject missing; escalating to human operator",
                ),
            )
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={
                "action": request.action,
                "summary": f"[support-v1 skeleton] Processed {request.action!r} "
                f"for subject: {subject}",
            },
            confidence=0.6,
            metadata={"channel": request.context.channel},
        )


def create_support_agent() -> SupportAgent:
    return SupportAgent()
