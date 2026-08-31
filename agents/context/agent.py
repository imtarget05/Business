"""Context Agent — conversational context memory (in-memory per-org store).

Capabilities: context.get, context.summarize, context.clear
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

SUPPORTED_ACTIONS = {"get", "summarize", "clear", "append"}

# In-memory store: org_id -> list[dict]
_CONTEXT_STORE: dict[str, list[dict[str, Any]]] = defaultdict(list)


class ContextAgent:
    def __init__(
        self, descriptor: AgentDescriptor | None = None, llm: LLMProvider | None = None
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="context",
            domain=Domain.CONTEXT,
            version="1",
            description="Manages conversational context memory per organization.",
            capabilities=frozenset(
                {"context.get", "context.summarize", "context.clear", "context.append"}
            ),
        )
        self._llm = llm or MockLLMProvider()

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"unsupported action {request.action!r} for context-v1",
                ),
            )
        org = str(request.context.organization_id or "default")
        if request.action == "append":
            msg = request.payload.get("message") or request.payload.get("content")
            if not msg:
                return AgentResponse(
                    task_id=request.task_id,
                    agent=self.descriptor.qualified_name,
                    status=AgentResponseStatus.REJECTED,
                    error=ErrorDetail(code="VALIDATION_ERROR", message="payload.message required"),
                )
            _CONTEXT_STORE[org].append(
                {"role": request.payload.get("role", "user"), "content": str(msg)}
            )
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"appended": True, "count": len(_CONTEXT_STORE[org])},
            )
        if request.action == "get":
            limit = int(request.payload.get("limit", 20) or 20)
            items = _CONTEXT_STORE[org][-limit:]
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"messages": items, "count": len(items), "total": len(_CONTEXT_STORE[org])},
            )
        if request.action == "clear":
            _CONTEXT_STORE[org].clear()
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"cleared": True},
            )
        if request.action == "summarize":
            items = _CONTEXT_STORE[org]
            if not items:
                return AgentResponse(
                    task_id=request.task_id,
                    agent=self.descriptor.qualified_name,
                    status=AgentResponseStatus.SUCCESS,
                    result={"summary": "no context", "count": 0},
                )
            # Use LLM to summarize if available, else simple concat
            try:
                text = "\n".join(f"{m['role']}: {m['content']}" for m in items[-20:])
                summary = await self._llm.generate(
                    prompt=f"Summarize this conversation:\n{text}",
                    system="You are a context summarizer.",
                )
                summary_text = summary if isinstance(summary, str) else str(summary)
            except Exception:
                summary_text = (
                    f"Context with {len(items)} messages, last: {items[-1]['content'][:200]}"
                )
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"summary": summary_text, "count": len(items)},
            )


def create_context_agent(llm: LLMProvider | None = None) -> ContextAgent:
    return ContextAgent(llm=llm)
