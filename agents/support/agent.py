"""Support Agent with tool integration (Phase 3, Task 3.3).

Phase 4 Task 4.2: Support for handoff to knowledge agent when
payload.needs_knowledge=true.
"""

from __future__ import annotations

from agents.support.tools import create_support_tools
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)
from packages.core.errors import ToolExecutionError
from packages.core.tools import ToolRegistry, execute_tool_loop
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

SUPPORTED_ACTIONS = {
    "triage",
    "draft_reply",
    "create_ticket",
    "lookup_customer",
    "send_email",
    "send_gmail",
}


class SupportAgent:
    def __init__(
        self,
        descriptor: AgentDescriptor | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="support",
            domain=Domain.SUPPORT,
            version="1",
            description="Triage inbound support requests, draft replies, and "
            "manage tickets/customers via tools.",
            capabilities=frozenset(
                {
                    "support.triage",
                    "support.draft_reply",
                    "support.create_ticket",
                    "support.lookup_customer",
                    "support.send_email",
                    "support.send_gmail",
                }
            ),
        )
        self._llm = llm or MockLLMProvider()
        self._tools = create_support_tools()
        self._registry = ToolRegistry(*self._tools)

    @property
    def llm(self) -> LLMProvider:
        return self._llm

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

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

        # Check if this support task needs knowledge (handoff trigger)
        needs_knowledge = request.payload.get("needs_knowledge", False)
        if needs_knowledge:
            # Signal to orchestrator to handoff to knowledge.query
            # The actual handoff is performed by the orchestrator after this response
            question = str(request.payload.get("question", "")).strip()
            if not question:
                # If no explicit question, use subject+body as the question
                body = str(request.payload.get("body", "")).strip()
                question = f"{subject}. {body}".strip()
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={
                    "action": request.action,
                    "summary": "Knowledge lookup required",
                    "needs_knowledge": True,
                    "knowledge_question": question,
                },
                confidence=0.5,
                metadata={
                    "channel": request.context.channel,
                    "handoff": {
                        "target_capability": "knowledge.query",
                        "reason": "Support task requires knowledge base lookup",
                        "question": question,
                    },
                },
            )

        # Build a prompt that includes the action and payload
        prompt = self._build_prompt(request)

        try:
            result = await execute_tool_loop(
                provider=self._llm,
                prompt=prompt,
                registry=self._registry,
                system=self._system_prompt(),
                organization_id=request.context.organization_id,
            )
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"action": request.action, "summary": result},
                confidence=0.8,
                metadata={"channel": request.context.channel},
            )
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(f"Tool execution failed: {e}") from e

    def _system_prompt(self) -> str:
        return (
            "You are a support agent. Use the available tools to help customers. "
            "Available tools: send_email_reply (drafts or sends email via SMTP), "
            "send_gmail_reply (drafts or sends email via Gmail API + Sheets logging), "
            "create_ticket (creates a ticket), "
            "lookup_customer (CRUD on customers). All tools are org-scoped: the "
            "organization is injected server-side; never supply an "
            "organization_id argument."
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        payload = request.payload
        org_id = request.context.organization_id or "unknown"
        channel = request.context.channel

        base = (
            f"Action: {request.action}\\n"
            f"Organization: {org_id}\\n"
            f"Channel: {channel}\\n"
            f"Subject: {payload.get('subject', '')}\\n"
            f"Body: {payload.get('body', '')}\\n"
        )

        if request.action == "triage":
            return base + "\\nTriage this request and decide next steps. Use tools if needed."
        elif request.action == "draft_reply":
            return base + "\\nDraft a reply to the customer. Use send_email_reply in DRY-RUN mode."
        elif request.action == "create_ticket":
            customer_id = payload.get("customer_id")
            return base + f"\\nCreate a ticket for customer {customer_id}. Use create_ticket tool."
        elif request.action == "lookup_customer":
            return base + "\\nLook up or manage customer record. Use lookup_customer tool."
        elif request.action == "send_email":
            return base + "\\nSend an email reply. Use send_email_reply tool."
        elif request.action == "send_gmail":
            return base + "\\nSend an email reply via Gmail API. Use send_gmail_reply tool."
        return base

    def script_tool_calls(self, *outputs: str | dict) -> None:
        """Convenience for tests: script the underlying mock LLM."""
        if isinstance(self._llm, MockLLMProvider):
            self._llm.script(*outputs)
        else:
            raise TypeError("script_tool_calls only works with MockLLMProvider")


def create_support_agent(llm: LLMProvider | None = None) -> SupportAgent:
    return SupportAgent(llm=llm)