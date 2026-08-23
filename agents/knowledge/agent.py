"""Knowledge Agent skeleton (full RAG arrives in Phase 2 — not Phase 0)."""

from __future__ import annotations

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    Citation,
    ErrorDetail,
    TaskRequest,
)


class KnowledgeAgent:
    def __init__(self, descriptor: AgentDescriptor | None = None) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            version="1",
            description="Answers questions from the internal knowledge base "
            "(Phase 0 skeleton: echoes query, no retrieval yet).",
            capabilities=frozenset({"knowledge.query", "knowledge.summarize"}),
        )

    async def handle(self, request: TaskRequest) -> AgentResponse:
        question = str(request.payload.get("question", "")).strip()
        if not question:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="payload.question is required for knowledge.query",
                ),
            )
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={
                "answer": "[knowledge-v1 skeleton] Received your question; "
                "the retrieval pipeline is implemented in Phase 2.",
                "question": question,
            },
            citations=[
                Citation(source_id="placeholder-doc", title="Placeholder source (Phase 0)")
            ],
            confidence=0.5,
            metadata={"requires_citations": True},
        )


def create_knowledge_agent() -> KnowledgeAgent:
    return KnowledgeAgent()
