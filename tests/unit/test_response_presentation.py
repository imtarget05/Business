"""Unit tests: friendly response presentation layer."""

from __future__ import annotations

from uuid import uuid4

from packages.contracts.enums import AgentResponseStatus
from packages.contracts.models import AgentResponse, Citation
from packages.core.response_presentation import confidence_label, present


def _resp(**kw) -> AgentResponse:
    base = {
        "task_id": uuid4(),
        "agent": "research-v1",
        "status": AgentResponseStatus.SUCCESS,
        "result": {},
    }
    base.update(kw)
    return AgentResponse(**base)


class TestConfidenceLabel:
    def test_high(self) -> None:
        assert confidence_label(0.9) == "Tự tin cao"

    def test_medium(self) -> None:
        assert confidence_label(0.6) == "Khá tin"

    def test_low(self) -> None:
        assert confidence_label(0.3) == "Cần kiểm chứng"


class TestPresent:
    def test_answer_from_summary(self) -> None:
        out = present(_resp(result={"summary": "Nội dung tóm tắt"}, confidence=0.9))
        assert out["answer"] == "Nội dung tóm tắt"
        assert out["confidence_label"] == "Tự tin cao"
        assert out["next_suggested_actions"]

    def test_citations_become_evidence(self) -> None:
        resp = _resp(
            result={"answer": "Trả lời"},
            citations=[Citation(source_id="d1", title="Doc", uri="https://x", snippet="s")],
        )
        out = present(resp)
        assert out["evidence"][0]["title"] == "Doc"
        assert out["evidence"][0]["uri"] == "https://x"

    def test_key_points_extracted(self) -> None:
        out = present(_resp(result={"key_points": ["điểm 1", "điểm 2"]}))
        assert out["key_points"] == ["điểm 1", "điểm 2"]

    def test_escalated_has_friendly_actions(self) -> None:
        from packages.contracts.models import ErrorDetail

        out = present(
            _resp(
                status=AgentResponseStatus.ESCALATED,
                error=ErrorDetail(code="ESCALATED", message="no confident intent"),
            )
        )
        assert out["status"] == "escalated"
        assert out["next_suggested_actions"]

    def test_failed_friendly(self) -> None:
        from packages.contracts.models import ErrorDetail

        out = present(
            _resp(status=AgentResponseStatus.FAILED, error=ErrorDetail(code="X", message="y"))
        )
        assert out["status"] == "failed"
        assert "Thử lại" in out["next_suggested_actions"][0]

    def test_results_become_evidence(self) -> None:
        out = present(
            _resp(
                result={
                    "answer": "a",
                    "results": [{"url": "https://y", "title": "T", "snippet": "s"}],
                }
            )
        )
        assert any(ev["uri"] == "https://y" for ev in out["evidence"])
