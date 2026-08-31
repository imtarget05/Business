"""Phase 5 — Reporting Agent 5-step analysis chain (TDD).

Covers:
- report.generate orchestrates 5 internal LLM steps and returns full report
- empty metrics -> VALIDATION_ERROR rejection
- step failure -> typed error, no partial report claimed as complete
- sheet log called when flag enabled (mocked)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from agents.reporting.agent import ReportingAgent, create_reporting_agent
from packages.config.settings import get_settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.llm.mock import MockLLMProvider


def _request(metrics: dict, org_id=None) -> TaskRequest:
    return TaskRequest(
        domain=Domain.REPORT,
        action="generate",
        payload={"metrics": metrics},
        context=TaskContext(organization_id=org_id or uuid4()),
    )


# --- Test constants ---

# Step 1: COLLECT just echoes, no LLM call needed

# Step 2: ANALYZE response
ANALYZE_RESP = {
    "trends": [
        {"metric": "revenue", "direction": "up", "magnitude": 0.15},
        {"metric": "churn", "direction": "down", "magnitude": 0.08},
        {"metric": "latency_p99", "direction": "flat", "magnitude": 0.0},
    ]
}

# Step 3: ROOT_CAUSE response
ROOT_CAUSE_RESP = {
    "causes": [
        {
            "metric": "revenue",
            "cause": "New enterprise deals closed in Q3",
            "evidence": "Revenue jumped 15% correlating with 3 new logos",
        },
        {
            "metric": "churn",
            "cause": "Improved onboarding flow reduced early drop-off",
            "evidence": "Churn down 8% since onboarding v2 launch",
        },
    ]
}

# Step 4: RECOMMEND response
RECOMMEND_RESP = {
    "actions": [
        {
            "priority": "high",
            "action": "Double down on enterprise sales motion",
            "rationale": "Enterprise deals drive disproportionate revenue growth",
        },
        {
            "priority": "medium",
            "action": "Roll out onboarding v2 to all segments",
            "rationale": "Proven churn reduction in pilot segment",
        },
    ]
}

# Step 5: REPORT response
REPORT_RESP = {
    "summary": "Revenue up 15% driven by enterprise deals; churn down 8% from onboarding improvements. System latency stable.",
    "highlights": [
        "Revenue +15% QoQ",
        "Churn -8% QoQ",
        "Latency P99 stable at 145ms",
    ],
    "concerns": [
        "Enterprise concentration risk (top 3 accounts = 40% revenue)",
        "SMB segment growth flat",
    ],
    "recommendations": [
        "Double down on enterprise sales motion",
        "Roll out onboarding v2 to all segments",
        "Diversify revenue base with SMB campaigns",
    ],
}

SCRIPTED_RESPONSES = [
    ANALYZE_RESP,
    ROOT_CAUSE_RESP,
    RECOMMEND_RESP,
    REPORT_RESP,
]


class TestReportingAgent:
    """Tests for the ReportingAgent 5-step chain."""

    @pytest.fixture
    def llm(self) -> MockLLMProvider:
        """Mock LLM with scripted responses for all 4 LLM steps."""
        return MockLLMProvider(scripted=SCRIPTED_RESPONSES)

    @pytest.fixture
    def agent(self, llm: MockLLMProvider) -> ReportingAgent:
        return ReportingAgent(llm=llm)

    @pytest.mark.asyncio
    async def test_generate_full_report_structure(
        self, agent: ReportingAgent, llm: MockLLMProvider
    ) -> None:
        """Mock LLM scripted responses for 5 steps -> full report structure returned."""
        metrics = {
            "revenue": {"current": 1_150_000, "previous": 1_000_000},
            "churn": {"current": 0.04, "previous": 0.048},
            "latency_p99": {"current": 145, "previous": 144},
        }

        resp = await agent.handle(_request(metrics))

        # Verify success
        assert resp.status == AgentResponseStatus.SUCCESS
        assert resp.agent == "reporting-v1"

        # Verify all 5 steps present in result
        result = resp.result
        assert "collect" in result
        assert "analyze" in result
        assert "root_cause" in result
        assert "recommend" in result
        assert "report" in result

        # Verify collect step
        assert result["collect"]["metric_count"] == 3
        assert result["collect"]["metrics"] == metrics

        # Verify analyze step
        assert len(result["analyze"]["trends"]) == 3
        trend = result["analyze"]["trends"][0]
        assert trend["metric"] == "revenue"
        assert trend["direction"] == "up"
        assert trend["magnitude"] == 0.15

        # Verify root_cause step
        assert len(result["root_cause"]["causes"]) == 2
        cause = result["root_cause"]["causes"][0]
        assert cause["metric"] == "revenue"
        assert "enterprise" in cause["cause"].lower()

        # Verify recommend step
        assert len(result["recommend"]["actions"]) == 2
        action = result["recommend"]["actions"][0]
        assert action["priority"] == "high"
        assert "enterprise" in action["action"].lower()

        # Verify report step (final merged report)
        report = result["report"]
        assert "summary" in report
        assert len(report["highlights"]) == 3
        assert len(report["concerns"]) == 2
        assert len(report["recommendations"]) == 3
        assert "Revenue up 15%" in report["summary"]

        # Verify LLM was called 4 times (analyze, root_cause, recommend, report)
        assert len(llm.calls) == 4
        schemas = [call["schema"] for call in llm.calls]
        assert schemas == ["AnalyzeOut", "RootCauseOut", "RecommendOut", "FinalReport"]

    @pytest.mark.asyncio
    async def test_empty_metrics_rejected(self, agent: ReportingAgent) -> None:
        """Empty metrics -> VALIDATION_ERROR rejection."""
        resp = await agent.handle(_request({}))
        assert resp.status == AgentResponseStatus.REJECTED
        assert resp.error is not None
        assert resp.error.code == "VALIDATION_ERROR"
        assert "metrics" in resp.error.message.lower()

        # Also test missing metrics key
        req = TaskRequest(
            domain=Domain.REPORT,
            action="generate",
            payload={},
            context=TaskContext(organization_id=uuid4()),
        )
        resp2 = await agent.handle(req)
        assert resp2.status == AgentResponseStatus.REJECTED
        assert resp2.error.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_invalid_metrics_type_rejected(self, agent: ReportingAgent) -> None:
        """Non-dict metrics -> VALIDATION_ERROR."""
        req = TaskRequest(
            domain=Domain.REPORT,
            action="generate",
            payload={"metrics": "not a dict"},
            context=TaskContext(organization_id=uuid4()),
        )
        resp = await agent.handle(req)
        assert resp.status == AgentResponseStatus.REJECTED
        assert resp.error.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_step_failure_returns_typed_error_no_partial(self, agent: ReportingAgent) -> None:
        """Step failure -> typed error, no partial report claimed as complete."""
        # Create an LLM that fails on the second call (root_cause)
        failing_llm = MockLLMProvider(scripted=[ANALYZE_RESP, Exception("LLM timeout")])
        failing_agent = ReportingAgent(llm=failing_llm)

        metrics = {"revenue": {"current": 100, "previous": 90}}
        resp = await failing_agent.handle(_request(metrics))

        # Should fail, not return partial success
        assert resp.status == AgentResponseStatus.FAILED
        assert resp.error is not None
        assert resp.error.code == "INTERNAL_ERROR"
        assert "Report generation failed" in resp.error.message
        # Result should be empty or not claimed as complete
        assert not resp.result or "report" not in resp.result

    @pytest.mark.asyncio
    async def test_unsupported_action_rejected(self, agent: ReportingAgent) -> None:
        """Unsupported action -> VALIDATION_ERROR."""
        req = TaskRequest(
            domain=Domain.REPORT,
            action="unknown_action",
            payload={"metrics": {"x": 1}},
            context=TaskContext(organization_id=uuid4()),
        )
        resp = await agent.handle(req)
        assert resp.status == AgentResponseStatus.REJECTED
        assert resp.error.code == "VALIDATION_ERROR"
        assert "unsupported action" in resp.error.message

    @pytest.mark.asyncio
    async def test_sheet_log_called_when_flag_enabled(self) -> None:
        """Sheet log called when flag enabled (mocked)."""
        settings = get_settings()

        # Temporarily enable the flag
        original_flag = settings.reporting_sheet_log_enabled
        settings.reporting_sheet_log_enabled = True

        try:
            with patch(
                "agents.reporting.agent.sheet_log_row", new_callable=AsyncMock
            ) as mock_sheet:
                llm = MockLLMProvider(scripted=SCRIPTED_RESPONSES)
                agent = ReportingAgent(llm=llm)

                metrics = {"revenue": {"current": 100, "previous": 90}}
                resp = await agent.handle(_request(metrics))

                assert resp.status == AgentResponseStatus.SUCCESS
                # Verify sheet_log_row was called
                mock_sheet.assert_awaited_once()
                # Verify it was called with a list of 5 values
                call_args = mock_sheet.call_args[0][0]
                assert isinstance(call_args, list)
                assert len(call_args) == 5
        finally:
            # Restore flag
            settings.reporting_sheet_log_enabled = original_flag

    @pytest.mark.asyncio
    async def test_sheet_log_not_called_when_flag_disabled(self) -> None:
        """Sheet log NOT called when flag disabled (default)."""
        settings = get_settings()
        assert settings.reporting_sheet_log_enabled is False  # default

        with patch("agents.reporting.agent.sheet_log_row", new_callable=AsyncMock) as mock_sheet:
            llm = MockLLMProvider(scripted=SCRIPTED_RESPONSES)
            agent = ReportingAgent(llm=llm)

            metrics = {"revenue": {"current": 100, "previous": 90}}
            resp = await agent.handle(_request(metrics))

            assert resp.status == AgentResponseStatus.SUCCESS
            mock_sheet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sheet_log_failure_does_not_fail_report(self) -> None:
        """Sheet logging failure is best-effort; doesn't fail the report."""
        settings = get_settings()
        original_flag = settings.reporting_sheet_log_enabled
        settings.reporting_sheet_log_enabled = True

        try:
            with patch(
                "agents.reporting.agent.sheet_log_row",
                side_effect=Exception("Sheets API down"),
            ):
                llm = MockLLMProvider(scripted=SCRIPTED_RESPONSES)
                agent = ReportingAgent(llm=llm)

                metrics = {"revenue": {"current": 100, "previous": 90}}
                resp = await agent.handle(_request(metrics))

                # Report should still succeed
                assert resp.status == AgentResponseStatus.SUCCESS
                assert "report" in resp.result
        finally:
            settings.reporting_sheet_log_enabled = original_flag


class TestReportingAgentIntegration:
    """Integration-style tests via the orchestrator/bootstrap."""

    @pytest.mark.asyncio
    async def test_orchestrator_routes_report_generate(self) -> None:
        """End-to-end: orchestrator routes report.generate to ReportingAgent."""
        from packages.config.settings import Settings
        from packages.core.bootstrap import build_container, set_container

        # Build a test container with mock LLM
        from packages.llm.mock import MockLLMProvider

        test_settings = Settings(
            llm_provider="mock",
            persistence_enabled=False,
        )
        container = build_container(settings=test_settings)
        set_container(container)

        try:
            orchestrator = container.orchestrator
            req = _request(
                {"revenue": {"current": 100, "previous": 90}},
                org_id=uuid4(),
            )

            # Script the container's LLM (shared mock)
            mock_llm = container.orchestrator._llm
            if isinstance(mock_llm, MockLLMProvider):
                # First call is orchestrator.classify(), then 4 ReportingAgent steps
                mock_llm.script("report.generate", *SCRIPTED_RESPONSES)

            resp = await orchestrator.execute(req)

            assert resp.status == AgentResponseStatus.SUCCESS
            assert resp.agent == "reporting-v1"
            assert "report" in resp.result
        finally:
            set_container(None)


class TestReportingAgentFactory:
    """Tests for the create_reporting_agent factory."""

    def test_factory_creates_agent(self) -> None:
        agent = create_reporting_agent()
        assert isinstance(agent, ReportingAgent)
        assert agent.descriptor.name == "reporting"
        assert agent.descriptor.domain == Domain.REPORT
        assert "report.generate" in agent.descriptor.capabilities

    def test_factory_with_custom_llm(self) -> None:
        custom_llm = MockLLMProvider()
        agent = create_reporting_agent(llm=custom_llm)
        assert agent.llm is custom_llm
