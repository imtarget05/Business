"""Unit tests: escalation suggestions + conversation linking (friendly UX)."""

from __future__ import annotations

from uuid import uuid4

from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.router import score_candidates


class _Descriptor:
    def __init__(self, name: str, domain: str, capabilities: set[str]) -> None:
        self.qualified_name = f"{name}-v1"
        self.domain = type("D", (), {"value": domain})()
        self.capabilities = capabilities


class _Registry:
    def __init__(self, descriptors: list) -> None:
        self._descriptors = descriptors

    def list_agents(self) -> list:
        return self._descriptors


class TestEscalationSuggestions:
    def test_candidates_feed_suggested_intents(self) -> None:
        reg = _Registry(
            [
                _Descriptor("research", "research", {"research.web_search"}),
                _Descriptor("reporting", "report", {"report.generate"}),
            ]
        )
        scored = score_candidates("tìm nghiên cứu thị trường", reg)
        assert scored, "expected at least one candidate for research text"
        assert scored[0][0] == "research-v1"


class TestConversationLinking:
    def test_friendly_routing_uses_domain_enum(self) -> None:
        req = TaskRequest(
            domain=Domain.SUPPORT,
            action="triage",
            payload={"text": "hello"},
            context=TaskContext(conversation_id=uuid4()),
        )
        assert req.context.conversation_id is not None

    def test_default_context_has_no_conversation(self) -> None:
        req = TaskRequest(
            domain=Domain.SUPPORT,
            action="triage",
            payload={},
        )
        assert req.context.conversation_id is None
