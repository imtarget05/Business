"""Reflection Engine (Phase 2): LLM auto-critique of agent outputs.

Fire-and-forget after a task completes: scores the response
(relevance/completeness) and flags issues. Written into the learning loop's
feedback records. MockLLM-safe.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from packages.llm.base import LLMProvider
from packages.observability.logging import get_logger

logger = get_logger("reflection")


class _Critique(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


_SYSTEM = (
    "You are a strict output reviewer for an agent system. "
    "Given a task and its agent response, score overall quality 0.0-1.0 "
    "(relevance + completeness) and list concrete issues. Be conservative."
)


class ReflectionEngine:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def critique(
        self,
        task_id: str,
        capability: str,
        request_text: str,
        response_text: str,
    ) -> dict[str, Any]:
        """Return {"score": float, "issues": [...]} — never raises."""
        prompt = (
            f"TASK_CAPABILITY: {capability}\n"
            f"USER_TASK: {request_text[:1500]}\n"
            f"AGENT_RESPONSE: {response_text[:2000]}\n"
            "Score the response and list issues."
        )
        try:
            raw = await self._llm.generate_structured(
                prompt, schema=_Critique, system=_SYSTEM, temperature=0.0, max_tokens=256
            )
            return {"score": raw.score, "issues": raw.issues}
        except Exception as exc:  # noqa: BLE001 — reflection must not break pipeline
            logger.warning(
                "critique_failed",
                extra={"task_id": task_id, "error": type(exc).__name__},
            )
            return {"score": -1.0, "issues": [f"critique_unavailable:{type(exc).__name__}"]}


__all__ = ["ReflectionEngine"]
