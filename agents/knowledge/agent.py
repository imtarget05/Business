"""Knowledge Agent — RAG answer loop (Phase 2 Task 2.4).

Flow: embed question -> retrieve above hard threshold -> if no context:
return "no relevant information found" WITHOUT calling the LLM; else prompt
the LLM with retrieved context and return answer + citations.
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
from packages.database.repositories.documents import KnowledgeRepository
from packages.llm.base import EmbeddingProvider, LLMProvider

DEFAULT_SIMILARITY_THRESHOLD = 0.75
NO_INFO_ANSWER = "no relevant information found"


class _AnswerOut(BaseModel):
    answer: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class KnowledgeAgent:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository | None = None,
        llm: LLMProvider | None = None,
        embeddings: EmbeddingProvider | None = None,
        descriptor: AgentDescriptor | None = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        repo_factory=None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            version="1",
            description="Answers questions from the internal knowledge base "
            "with citations; refuses to guess without relevant context.",
            capabilities=frozenset(
                {"knowledge.query", "knowledge.summarize", "knowledge.delete"}
            ),
        )
        self._repo = repository
        self._llm = llm
        self._embeddings = embeddings
        self._threshold = similarity_threshold
        self._repo_factory = repo_factory

    async def _get_repo(self) -> KnowledgeRepository | None:
        if self._repo is not None:
            return self._repo
        if self._repo_factory is not None:
            return await self._repo_factory()
        return None

    async def handle(self, request: TaskRequest) -> AgentResponse:
        question = str(request.payload.get("question", "")).strip()
        repo = await self._get_repo() if question else None
        if not question or repo is None or self._llm is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="payload.question is required for knowledge.query",
                ),
            )

        org_id = request.context.organization_id
        if org_id is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="context.organization_id is required for knowledge.query",
                ),
            )
        query_vec = None
        if self._embeddings is not None:
            query_vec = (await self._embeddings.embed([question]))[0]
        hits = await repo.search(
            organization_id=org_id,
            query=question,
            top_k=4,
            threshold=self._threshold,
            query_embedding=query_vec,
        )

        if not hits:
            # HARD RULE: never let the LLM answer from weak/no context.
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"answer": NO_INFO_ANSWER, "confidence": 0.0},
                citations=[],
                metadata={"retrieval": "below_threshold"},
            )

        context_blocks = []
        citations: list[Citation] = []
        for i, (chunk, _score) in enumerate(hits, start=1):
            doc_title = chunk.document.title if chunk.document else "document"
            context_blocks.append(f"[{i}] {doc_title}: {chunk.content}")
            citations.append(
                Citation(
                    source_id=str(chunk.document_id),
                    title=doc_title,
                    snippet=chunk.content[:200],
                )
            )
        context_text = "\n\n".join(context_blocks)

        raw = await self._llm.generate_structured(
            _build_prompt(question),
            schema=_AnswerOut,
            system=(
                "Answer ONLY from the provided context. Cite blocks as [n].\n\n"
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
            confidence=max(raw.confidence, min(s for _, s in hits)),
            metadata={"retrieval_hits": len(hits)},
        )


def _build_prompt(question: str) -> str:
    return f"Question: {question}"


# Backwards-compatible factory used by bootstrap/scripts.
def create_knowledge_agent(**kwargs: Any) -> KnowledgeAgent:
    return KnowledgeAgent(**kwargs)


__all__ = [
    "KnowledgeAgent",
    "create_knowledge_agent",
    "NO_INFO_ANSWER",
    "DEFAULT_SIMILARITY_THRESHOLD",
]
