"""Reporting Agent — 5-step analysis chain (Phase 5).

Flow: COLLECT -> ANALYZE -> ROOT_CAUSE -> RECOMMEND -> REPORT

Each step uses the LLM via generate_structured to produce structured output.
All steps are internal to this agent (no cross-agent handoffs).
Optional: appends a summary row to Google Sheets when
settings.reporting_sheet_log_enabled=True and google_sheet_id is configured.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from packages.config.settings import get_settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

try:
    from integrations.google_client import sheet_log_row
except ImportError:  # optional dependency (google-auth) not installed in minimal image
    sheet_log_row = None  # type: ignore

SUPPORTED_ACTIONS = {"generate"}


# --- Step output schemas -------------------------------------------------------


class CollectOut(BaseModel):
    """COLLECT step: normalized metrics from payload."""

    metrics: dict[str, Any] = Field(default_factory=dict)
    metric_count: int = 0
    note: str = ""


class TrendItem(BaseModel):
    metric: str
    direction: str  # "up" | "down" | "flat"
    magnitude: float  # relative change, 0.0-1.0+


class AnalyzeOut(BaseModel):
    """ANALYZE step: detected trends per metric."""

    trends: list[TrendItem] = Field(default_factory=list)


class CauseItem(BaseModel):
    metric: str
    cause: str
    evidence: str


class RootCauseOut(BaseModel):
    """ROOT_CAUSE step: hypothesized causes with evidence."""

    causes: list[CauseItem] = Field(default_factory=list)


class ActionItem(BaseModel):
    priority: str  # "high" | "medium" | "low"
    action: str
    rationale: str


class RecommendOut(BaseModel):
    """RECOMMEND step: prioritized remediation actions."""

    actions: list[ActionItem] = Field(default_factory=list)


class FinalReport(BaseModel):
    """REPORT step: final merged report."""

    summary: str
    highlights: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# --- Agent ----------------------------------------------------------------------


class ReportingAgent:
    def __init__(
        self,
        *,
        descriptor: AgentDescriptor | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="reporting",
            domain=Domain.REPORT,
            version="1",
            description="Generates operational reports from metrics via a 5-step "
            "analysis chain: collect, analyze, root-cause, recommend, report.",
            capabilities=frozenset({"report.generate"}),
        )
        self._llm = llm or MockLLMProvider()
        self._last_org_id: Any = None  # last organization_id seen, for isolation tests

    @property
    def llm(self) -> LLMProvider:
        return self._llm

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"unsupported action {request.action!r} for reporting-v1",
                ),
            )

        metrics = request.payload.get("metrics")
        if not metrics or not isinstance(metrics, dict):
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="payload.metrics (dict) is required for report.generate",
                ),
            )

        # Execute the 5-step chain
        try:
            result = await self._execute_chain(request, metrics)
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message=f"Report generation failed: {e}",
                ),
            )

        # Optional: log to Google Sheets
        if get_settings().reporting_sheet_log_enabled:
            try:
                await self._log_to_sheets(result)
            except Exception:
                # Sheet logging is best-effort; don't fail the report
                pass

        # Tenant-isolation: carry the originating org (if any) onto the result so
        # downstream storage / Sheets logging can scope the report to its tenant.
        # ReportingAgent must never mix data across organizations.
        org_id = request.context.organization_id
        if org_id is not None:
            result["organization_id"] = str(org_id)
            self._last_org_id = org_id

        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result=result,
            confidence=0.85,
            metadata={
                "steps_completed": 5,
                "organization_id": str(org_id) if org_id is not None else None,
            },
        )

    async def _execute_chain(
        self, request: TaskRequest, metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Run all 5 steps sequentially, passing data forward."""
        step_results: dict[str, Any] = {}

        # Step 1: COLLECT
        collect = await self._step_collect(metrics)
        step_results["collect"] = collect.model_dump()

        # Step 2: ANALYZE
        analyze = await self._step_analyze(collect)
        step_results["analyze"] = analyze.model_dump()

        # Step 3: ROOT_CAUSE
        root_cause = await self._step_root_cause(collect, analyze)
        step_results["root_cause"] = root_cause.model_dump()

        # Step 4: RECOMMEND
        recommend = await self._step_recommend(collect, analyze, root_cause)
        step_results["recommend"] = recommend.model_dump()

        # Step 5: REPORT
        report = await self._step_report(
            collect, analyze, root_cause, recommend
        )
        step_results["report"] = report.model_dump()

        return step_results

    # --- Step implementations ---------------------------------------------------

    async def _step_collect(self, metrics: dict[str, Any]) -> CollectOut:
        """Normalize and validate input metrics."""
        # In a real implementation, this might fetch from a time-series DB,
        # validate schema, compute derived fields, etc.
        # Here we just echo back with a count.
        return CollectOut(
            metrics=metrics,
            metric_count=len(metrics),
            note=f"Collected {len(metrics)} metric(s) from payload",
        )

    async def _step_analyze(self, collect: CollectOut) -> AnalyzeOut:
        """LLM: detect trends (up/down/flat) with magnitude per metric."""
        prompt = (
            "You are an operations analyst. Given the following metrics, "
            "identify trends for each metric. Return structured output.\n\n"
            f"METRICS:\n{json.dumps(collect.metrics, indent=2, default=str)}\n\n"
            "For each metric, determine:\n"
            "- direction: \"up\", \"down\", or \"flat\"\n"
            "- magnitude: relative change 0.0-1.0+ (e.g., 0.15 = 15% change)\n"
            "Return ONLY the structured trends array."
        )

        return await self._llm.generate_structured(
            prompt,
            schema=AnalyzeOut,
            system=(
                "You are a precise operations analyst. Output only valid JSON "
                "matching the schema. No extra commentary."
            ),
            temperature=0.0,
        )

    async def _step_root_cause(
        self, collect: CollectOut, analyze: AnalyzeOut
    ) -> RootCauseOut:
        """LLM: hypothesize root causes for concerning trends."""
        prompt = (
            "You are an operations analyst. Given these metrics and their trends, "
            "identify plausible root causes for any non-flat trends.\n\n"
            f"METRICS:\n{json.dumps(collect.metrics, indent=2, default=str)}\n\n"
            f"TRENDS:\n{json.dumps([t.model_dump() for t in analyze.trends], indent=2)}\n\n"
            "For each metric with a non-flat trend, provide:\n"
            "- cause: concise root cause hypothesis\n"
            "- evidence: which data points or patterns support this\n"
            "Return ONLY the structured causes array."
        )

        return await self._llm.generate_structured(
            prompt,
            schema=RootCauseOut,
            system=(
                "You are a precise operations analyst. Output only valid JSON "
                "matching the schema. No extra commentary."
            ),
            temperature=0.0,
        )

    async def _step_recommend(
        self,
        collect: CollectOut,
        analyze: AnalyzeOut,
        root_cause: RootCauseOut,
    ) -> RecommendOut:
        """LLM: recommend prioritized remediation actions."""
        prompt = (
            "You are an operations analyst. Given metrics, trends, and root causes, "
            "recommend concrete remediation actions.\n\n"
            f"METRICS:\n{json.dumps(collect.metrics, indent=2, default=str)}\n\n"
            f"TRENDS:\n{json.dumps([t.model_dump() for t in analyze.trends], indent=2)}\n\n"
            f"ROOT CAUSES:\n{json.dumps([c.model_dump() for c in root_cause.causes], indent=2)}\n\n"
            "For each concern, recommend an action with:\n"
            "- priority: \"high\", \"medium\", or \"low\"\n"
            "- action: specific, actionable step\n"
            "- rationale: why this addresses the root cause\n"
            "Return ONLY the structured actions array."
        )

        return await self._llm.generate_structured(
            prompt,
            schema=RecommendOut,
            system=(
                "You are a precise operations analyst. Output only valid JSON "
                "matching the schema. No extra commentary."
            ),
            temperature=0.0,
        )

    async def _step_report(
        self,
        collect: CollectOut,
        analyze: AnalyzeOut,
        root_cause: RootCauseOut,
        recommend: RecommendOut,
    ) -> FinalReport:
        """LLM: synthesize final human-readable report."""
        prompt = (
            "You are an operations analyst. Synthesize a concise executive report "
            "from the full analysis chain.\n\n"
            f"COLLECTED METRICS ({collect.metric_count}):\n"
            f"{json.dumps(collect.metrics, indent=2, default=str)}\n\n"
            f"TRENDS:\n{json.dumps([t.model_dump() for t in analyze.trends], indent=2)}\n\n"
            f"ROOT CAUSES:\n{json.dumps([c.model_dump() for c in root_cause.causes], indent=2)}\n\n"
            f"RECOMMENDATIONS:\n{json.dumps([a.model_dump() for a in recommend.actions], indent=2)}\n\n"
            "Produce a final report with:\n"
            "- summary: 2-3 sentence executive summary\n"
            "- highlights: 3-5 bullet points of positive/neutral findings\n"
            "- concerns: 3-5 bullet points of issues needing attention\n"
            "- recommendations: 3-5 bullet points of top actions (from recommend step)\n"
            "Return ONLY the structured report."
        )

        return await self._llm.generate_structured(
            prompt,
            schema=FinalReport,
            system=(
                "You are a precise operations analyst. Output only valid JSON "
                "matching the schema. No extra commentary."
            ),
            temperature=0.0,
        )

    async def _log_to_sheets(self, result: dict[str, Any]) -> None:
        """Append a summary row to the configured Google Sheet."""
        if sheet_log_row is None:
            return
        report = result.get("report", {})
        summary = report.get("summary", "")
        highlights = "; ".join(report.get("highlights", []))
        concerns = "; ".join(report.get("concerns", []))
        recommendations = "; ".join(report.get("recommendations", []))

        # Truncate to reasonable cell sizes
        values = [
            summary[:500],
            highlights[:500],
            concerns[:500],
            recommendations[:500],
            json.dumps(result.get("collect", {}).get("metrics", {}))[:500],
        ]

        await sheet_log_row(values)


def create_reporting_agent(llm: LLMProvider | None = None) -> ReportingAgent:
    return ReportingAgent(llm=llm)


__all__ = [
    "ReportingAgent",
    "create_reporting_agent",
    "SUPPORTED_ACTIONS",
]