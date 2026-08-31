"""Feedback API (learning loop, ADR-010)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from packages.core.bootstrap import get_container
from packages.core.errors import ValidationError
from packages.observability.logging import get_logger

logger = get_logger("feedback")

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    task_id: str = Field(min_length=8)
    rating: str | None = None
    corrected_capability: str | None = None
    comment: str | None = Field(default=None, max_length=2000)
    source: str = "api"


@router.post("", status_code=201)
async def submit_feedback(body: FeedbackIn, request: Request) -> dict[str, Any]:
    # Manual validation -> consistent 422 via BusinessOpsError handler.
    if body.rating is not None and body.rating not in {"up", "down"}:
        raise ValidationError("rating must be 'up' or 'down'")
    if body.corrected_capability is not None and "." not in body.corrected_capability:
        raise ValidationError("corrected_capability must be 'domain.action'")

    container = get_container()
    learning = container.learning
    await learning.record_feedback(
        {
            "task_id": body.task_id,
            "rating": body.rating,
            "corrected_capability": body.corrected_capability,
            "comment": body.comment,
            "source": body.source,
        }
    )
    logger.info("feedback_received", extra={"task_id": body.task_id, "rating": body.rating})
    return {"status": "recorded", "task_id": body.task_id}


@router.get("/stats")
async def feedback_stats() -> dict[str, Any]:
    container = get_container()
    rules = container.learning.get_rules()
    return {
        "rules_total": len(rules),
        "rules": [
            {"keyword": r.keyword, "capability": r.capability, "hits": r.hits} for r in rules[:50]
        ],
    }
