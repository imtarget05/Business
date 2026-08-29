"""Knowledge Agent — full-text Second Brain answer loop (Task 1).

Flow: retrieve top-k chunks from :class:`KnowledgeBase` (tsvector full-text,
**no embeddings**) -> if no relevant chunk: return "no relevant information
found" WITHOUT calling the LLM (hard rule: never answer without verified
context) -> else synthesize a cited answer via the LLM (container LLM).

Capability: ``knowledge.query``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    Citation,
    ErrorDetail,
    TaskRequest,
)
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.base import LLMProvider

DEFAULT_TOP_K = 5
NO_INFO_ANSWER = "no relevant information found"


class _AnswerOut(BaseModel):
    answer: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class KnowledgeAgent:
    def __init__(
        self,
        *,
        kb: KnowledgeBase | None = None,
        llm: LLMProvider | None = None,
        descriptor: AgentDescriptor | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            version="1",
            description=(
                "Answers questions from the internal knowledge base (Second Brain) "
                "using full-text retrieval + LLM synthesis; refuses to guess "
                "without relevant context."
            ),
            capabilities=frozenset({"knowledge.query"}),
        )
        self._kb = kb
        self._llm = llm
        self._top_k = top_k

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

        if self._kb is None or self._llm is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="CONFIGURATION_ERROR",
                    message="knowledge base / llm not configured for knowledge.query",
                ),
            )

        chunks = await self._kb.query(question, k=self._top_k)

        # HARD RULE: never let the LLM answer from weak/no context.
        if not chunks:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"answer": NO_INFO_ANSWER, "confidence": 0.0},
                citations=[],
                metadata={"retrieval": "no_match"},
            )

        context_blocks: list[str] = []
        citations: list[Citation] = []
        for i, chunk in enumerate(chunks, start=1):
            context_blocks.append(f"[{i}] {chunk}")
            citations.append(
                Citation(
                    source_id=f"kb-chunk-{i}",
                    title="Knowledge Base",
                    snippet=chunk[:200],
                )
            )
        context_text = "\n\n".join(context_blocks)

        raw = await self._llm.generate_structured(
            _build_prompt(question),
            schema=_AnswerOut,
            system=(
                "Answer ONLY from the provided context. Cite the supporting "
                "blocks as [n]. If the context does not contain the answer, say "
                "so. Be concise.\n\n"
                f"CONTEXT:\n{context_text}"
            ),
        )
        assert isinstance(raw, _AnswerOut)

        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={"answer": raw.answer, "confidence": raw.confidence},
            citations=citations,
            confidence=raw.confidence,
            metadata={"retrieval_hits": len(chunks)},
        )


def _build_prompt(question: str) -> str:
    return f"Question: {question}"


# Backwards-compatible factory used by bootstrap/scripts.
def create_knowledge_agent(
    *,
    kb: KnowledgeBase | None = None,
    llm: LLMProvider | None = None,
    top_k: int = DEFAULT_TOP_K,
    **kwargs: Any,
) -> KnowledgeAgent:
    return KnowledgeAgent(kb=kb, llm=llm, top_k=top_k)


__all__ = [
    "KnowledgeAgent",
    "create_knowledge_agent",
    "NO_INFO_ANSWER",
    "DEFAULT_TOP_K",
]
