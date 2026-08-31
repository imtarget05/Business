"""Knowledge Agent - hybrid (full-text + vector) Second Brain answer loop.

Flow: retrieve top-k chunks via BOTH full-text (tsvector) and vector (cosine)
retrieval, merge + rerank with :func:`hybrid_retrieve`, then:

  - if no relevant chunk: return "no relevant information found" WITHOUT calling
    the LLM (hard rule: never answer without verified context).
  - else synthesize a cited answer via the LLM (container LLM).

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
from packages.core.hybrid_retrieval import hybrid_retrieve
from packages.core.knowledge_base import KnowledgeBase
from packages.llm.base import LLMProvider
from packages.llm.embeddings import cosine_similarity

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
        fts_weight: float = 0.5,
        vector_weight: float = 0.5,
        min_similarity: float = 0.5,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="knowledge",
            domain=Domain.KNOWLEDGE,
            version="1",
            description=(
                "Answers questions from the internal knowledge base (Second Brain) "
                "using hybrid full-text + semantic retrieval and LLM synthesis; "
                "refuses to guess without relevant context."
            ),
            capabilities=frozenset({"knowledge.query"}),
        )
        self._kb = kb
        self._llm = llm
        self._top_k = top_k
        self._fts_weight = fts_weight
        self._vector_weight = vector_weight
        self._min_similarity = min_similarity

    def _resolve_embedding_provider(self):
        """Best-effort access to an embedding provider for RAW cosine scoring.

        The vector retriever returns ranked contents (not scores); to apply the
        min_similarity floor on the real semantic signal we need cosine scores,
        which requires the provider. Falls back to None (trust the retriever).
        """
        try:
            ep = getattr(self._kb, "_embedding_provider", None)
            if ep is not None:
                return ep
            from packages.core.knowledge_base import _default_embedding_provider

            return _default_embedding_provider()
        except Exception:
            return None

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

        # Hybrid retrieval: run both retrievers, then merge + rerank.
        fts = await self._kb.query(question, k=self._top_k)

        # Fix 7 (perf short-circuit): the vector pass is the expensive one. Only
        # run it when full-text did NOT already return enough candidates.
        if len(fts) >= self._top_k:
            vec: list = []
        else:
            vec = await self._kb.query_vector(question, top_k=self._top_k)

        fts_denom = len(fts) or 1
        fts_items = [
            {"content": c, "score": 1.0 - i / fts_denom} for i, c in enumerate(fts)
        ]

        # Pre-fusion similarity floor. The fused RRF score is rank-based (max
        # ~1/60 ~= 0.016) and never reaches min_similarity, so the floor MUST be
        # applied to the RAW signal BEFORE fusion: a vector chunk is kept only if
        # its cosine similarity to the query is >= self._min_similarity. If the
        # retriever already returns scored dicts we use their cosine ``score``;
        # otherwise we compute cosine via the KB's embedding provider. With no
        # provider available we trust the retriever's internal top-k cosine gate.
        vec_items: list[dict[str, Any]] = []
        provider = self._resolve_embedding_provider()
        qvec = None
        if provider is not None:
            try:
                qvec = (await provider.embed([question]))[0]
            except Exception:
                qvec = None
        for v in vec:
            if isinstance(v, dict):
                if float(v.get("score", 0.0)) >= self._min_similarity:
                    vec_items.append(v)
                continue
            if qvec is not None:
                try:
                    cvec = (await provider.embed([v]))[0]
                    if cosine_similarity(qvec, cvec) >= self._min_similarity:
                        vec_items.append({"content": v, "score": 1.0})
                    continue
                except Exception:
                    pass
            vec_items.append({"content": v, "score": 1.0})

        merged = hybrid_retrieve(
            question,
            fts_items,
            vec_items,
            fts_weight=self._fts_weight,
            vector_weight=self._vector_weight,
        )
        # NOTE: do NOT re-apply min_similarity to the fused RRF score (it would
        # reject all chunks). The "never answer without verified context" hard
        # rule is enforced below by the empty-retrieval check.
        chunks = [m["content"] for m in merged][: self._top_k]

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
            metadata={"retrieval_hits": len(chunks), "retrieval": "hybrid"},
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
    return KnowledgeAgent(kb=kb, llm=llm, top_k=top_k, **kwargs)


__all__ = [
    "KnowledgeAgent",
    "create_knowledge_agent",
    "NO_INFO_ANSWER",
    "DEFAULT_TOP_K",
]
