"""Friendly Response Presentation layer.

Normalizes the heterogeneous per-agent ``AgentResponse.result`` shapes into a
single user-facing envelope::

    {
        "answer": str,
        "key_points": [str],
        "evidence": [{title, uri, snippet}],
        "confidence_label": "Tự tin cao|Khá tin|Cần kiểm chứng",
        "next_suggested_actions": [str],
    }

Read-only: never mutates the underlying AgentResponse. Consumed by the
Telegram reporter and dashboard; additive to the API contract.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.enums import AgentResponseStatus
from packages.contracts.models import AgentResponse

_CONFIDENCE_LABELS = (
    (0.8, "Tự tin cao"),
    (0.5, "Khá tin"),
    (0.0, "Cần kiểm chứng"),
)


def confidence_label(confidence: float) -> str:
    for threshold, label in _CONFIDENCE_LABELS:
        if confidence >= threshold:
            return label
    return _CONFIDENCE_LABELS[-1][1]


def _extract_answer(response: AgentResponse) -> str:
    result = response.result or {}
    for key in ("answer", "summary", "report", "analysis", "content", "transcript"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # drafts / summaries nested under common keys
    for key in ("draft", "reply", "recommendation", "text"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if response.citations:
        return "Đã tìm thấy nguồn liên quan (xem Evidence bên dưới)."
    return "Đã xử lý yêu cầu." if response.status == AgentResponseStatus.SUCCESS else ""


def _extract_key_points(response: AgentResponse) -> list[str]:
    result = response.result or {}
    for key in ("key_points", "points", "findings", "insights"):
        value = result.get(key)
        if isinstance(value, list) and value:
            return [str(item)[:200] for item in value[:5]]
    return []


def _extract_evidence(response: AgentResponse) -> list[dict[str, Any]]:
    evidence = [{"title": c.title, "uri": c.uri, "snippet": c.snippet} for c in response.citations]
    result = response.result or {}
    for key in ("results", "evidence"):
        value = result.get(key)
        if isinstance(value, list) and value:
            for item in value[:5]:
                if isinstance(item, dict) and item.get("url"):
                    evidence.append(
                        {
                            "title": str(item.get("title", item.get("url", "")))[:120],
                            "uri": item.get("url"),
                            "snippet": str(item.get("snippet", ""))[:200],
                        }
                    )
    return evidence[:8]


_NEXT_ACTIONS: dict[AgentResponseStatus, list[str]] = {
    AgentResponseStatus.SUCCESS: ["Hỏi tiếp để làm sâu hơn", "Đánh giá 👍/👎 để tôi học"],
    AgentResponseStatus.ESCALATED: ["Diễn đạt lại yêu cầu", "Liên hệ người phụ trách"],
    AgentResponseStatus.REJECTED: ["Kiểm tra lại nội dung gửi", "Liên hệ quản trị viên"],
    AgentResponseStatus.FAILED: ["Thử lại sau ít phút", "Đánh giá 👎 để tôi ghi nhận sự cố"],
    AgentResponseStatus.TIMEOUT: ["Thử lại — yêu cầu đã quá thời gian chờ"],
}


def present(response: AgentResponse) -> dict[str, Any]:
    """Build the user-friendly envelope from any agent response."""
    status_label = {
        AgentResponseStatus.SUCCESS: "success",
        AgentResponseStatus.FAILED: "failed",
        AgentResponseStatus.REJECTED: "rejected",
        AgentResponseStatus.ESCALATED: "escalated",
        AgentResponseStatus.TIMEOUT: "timeout",
    }.get(response.status, "unknown")

    return {
        "status": status_label,
        "answer": _extract_answer(response),
        "key_points": _extract_key_points(response),
        "evidence": _extract_evidence(response),
        "confidence_label": confidence_label(response.confidence)
        if response.status == AgentResponseStatus.SUCCESS
        else "",
        "next_suggested_actions": _NEXT_ACTIONS.get(response.status, []),
        "agent": response.agent,
    }


__all__ = ["present", "confidence_label"]
