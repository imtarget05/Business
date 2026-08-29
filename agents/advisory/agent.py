"""AI Advisory Council Agent (Task 3).

Answers a business question through one of three expert *personas*
(Hormozi = strategy, Buffett = investing, GaryVee = marketing/finance). The
persona is a **system-prompt override** on the shared ``container.llm`` — there
is no separate model per expert (Ruling: personas are system-prompt overrides).

Capability: ``advisory.ask`` (domain ``advisory`` — ``Domain.ADVISORY``).

Design for testability
----------------------
The LLM is injected, so the unit test passes a :class:`MockLLMProvider` and
asserts: (1) the correct persona system prompt is applied, and (2) when no
persona is supplied the agent auto-detects one from the question text via
``packages.core.personas.select_persona``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)
from packages.core.personas import PERSONA_LABELS, PERSONAS, select_persona
from packages.llm.base import LLMProvider


class _AnswerOut(BaseModel):
    answer: str = Field(min_length=1)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class AdvisoryAgent:
    """Answers via an expert persona system-prompt override (no separate model)."""

    def __init__(
        self,
        *,
        llm: LLMProvider | None = None,
        descriptor: AgentDescriptor | None = None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="advisory",
            domain=Domain.ADVISORY,
            version="1",
            description=(
                "AI Advisory Council: answers a question through one of three "
                "expert personas (Hormozi=strategy, Buffett=investing, "
                "GaryVee=marketing/finance) as a system-prompt override over "
                "the shared LLM. Auto-detects the right persona from the text."
            ),
            capabilities=frozenset({"advisory.ask"}),
        )
        self._llm = llm

    def _resolve_persona(self, request: TaskRequest) -> str:
        """Pick the persona: explicit payload key, else auto-detect from text."""
        # Explicit persona (e.g. /advisory <persona> <question>).
        persona = request.payload.get("persona")
        if isinstance(persona, str) and persona in PERSONAS:
            return persona
        # Auto-detect from the question text.
        question = str(request.payload.get("question") or request.payload.get("text") or "")
        detected = select_persona(question)
        if detected is not None:
            return detected
        # Deterministic default when nothing matches: strategy (Hormozi).
        return "hormozi"

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action != "ask":
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"advisory only supports action 'ask', got {request.action!r}",
                ),
            )

        question = str(request.payload.get("question") or request.payload.get("text") or "").strip()
        if not question:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="payload.question (or payload.text) is required for advisory.ask",
                ),
            )

        if self._llm is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="CONFIGURATION_ERROR",
                    message="llm not configured for advisory.ask",
                ),
            )

        persona = self._resolve_persona(request)
        system_prompt = PERSONAS[persona]
        label = PERSONA_LABELS.get(persona, persona)

        raw = await self._llm.generate_structured(
            _build_prompt(question),
            schema=_AnswerOut,
            system=system_prompt,
            temperature=0.3,
            max_tokens=1024,
        )
        assert isinstance(raw, _AnswerOut)

        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={
                "persona": persona,
                "persona_label": label,
                "answer": raw.answer,
                "confidence": raw.confidence,
            },
            confidence=raw.confidence,
            metadata={"persona": persona, "auto_detected": "persona" not in request.payload},
        )

    async def ask(self, question: str, *, persona: str | None = None) -> AgentResponse:
        """Convenience entry point (mirrors knowledge/ops agents' public API)."""
        import uuid as _uuid

        return await self.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.ADVISORY,
                action="ask",
                payload={"question": question, **({"persona": persona} if persona else {})},
            )
        )


def _build_prompt(question: str) -> str:
    return f"Question: {question}"


def create_advisory_agent(
    *,
    llm: LLMProvider | None = None,
    **kwargs: Any,
) -> AdvisoryAgent:
    """Factory used by bootstrap / scripts (mirrors other agents)."""
    return AdvisoryAgent(llm=llm)


__all__ = [
    "AdvisoryAgent",
    "create_advisory_agent",
]
