"""Supply Chain Reporting Agent (Phase SC).

Generates summary reports and dashboards for supply chain operations.
Consolidates metrics from PO processing, approval workflows, and
inventory monitoring into actionable insights for management.

Data sources are STUBS — real data would come from:
- PO Agent output (processed POs)
- Approval Workflow decisions (approved/rejected/expired counts)
- Inventory Monitor snapshots (stock levels, alerts)

This agent operates on in-memory mock data for testing/demo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from packages.contracts.enums import AgentResponseStatus
from packages.contracts.models import AgentResponse, ErrorDetail, TaskRequest

logger = logging.getLogger(__name__)


class ReportType(StrEnum):
    """Types of supply chain reports."""

    DAILY_SUMMARY = "daily_summary"
    PO_PROCESSING = "po_processing"
    APPROVAL_STATS = "approval_stats"
    INVENTORY_ALERTS = "inventory_alerts"
    FULL_DASHBOARD = "full_dashboard"


class PriorityLevel(StrEnum):
    """Priority classification for report metrics."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class InventoryAlertSummary:
    """Summary of inventory alerts for reporting."""

    total_alerts: int = 0
    critical_alerts: int = 0
    warning_alerts: int = 0
    info_alerts: int = 0
    low_stock_items: list[str] = field(default_factory=list)
    out_of_stock_items: list[str] = field(default_factory=list)
    overstock_items: list[str] = field(default_factory=list)


@dataclass
class ApprovalStats:
    """Statistics from approval workflow decisions."""

    total_po_processed: int = 0
    total_approval_requests: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    expired_count: int = 0
    pending_count: int = 0
    approval_rate: float = 0.0
    avg_decision_time_hours: float = 0.0


@dataclass
class InventoryStatusSummary:
    """Summary of inventory health."""

    total_items_monitored: int = 0
    total_inventory_value: float = 0.0
    low_stock_count: int = 0
    out_of_stock_count: int = 0
    overstock_count: int = 0
    normal_count: int = 0
    low_stock_skus: list[str] = field(default_factory=list)
    out_of_stock_skus: list[str] = field(default_factory=list)
    overstock_skus: list[str] = field(default_factory=list)


@dataclass
class SupplyChainDashboard:
    """Complete supply chain operating dashboard."""

    report_type: ReportType = ReportType.FULL_DASHBOARD
    generated_at: str = ""
    period: str = "daily"
    total_pos_processed: int = 0
    total_pos_value: float = 0.0
    po_processing_summary: dict[str, Any] = field(default_factory=dict)
    approval_stats: ApprovalStats = field(default_factory=ApprovalStats)
    inventory_summary: InventoryStatusSummary = field(default_factory=InventoryStatusSummary)
    alert_summary: InventoryAlertSummary = field(default_factory=InventoryAlertSummary)
    health_score: int = 100
    insights: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SupplyChainReporter:
    """Agent that generates supply chain summary reports and dashboards.

    Consolidates metrics from PO processing, approval workflows,
    and inventory monitoring into actionable reports for management.

    For testing/demo, operates on in-memory mock data.
    """

    def __init__(self) -> None:
        self._mock_po_data: list[dict[str, Any]] = []
        self._mock_approval_data: list[dict[str, Any]] = []
        self._mock_inventory_data: list[dict[str, Any]] = []

    def add_mock_po(
        self,
        po_number: str,
        vendor: str,
        total: float,
        route: str,
        po_type: str,
        processed_at: str | None = None,
    ) -> None:
        self._mock_po_data.append(
            {
                "po_number": po_number,
                "vendor": vendor,
                "total": total,
                "route": route,
                "po_type": po_type,
                "processed_at": processed_at or "",
            }
        )

    def add_mock_approval(
        self,
        po_number: str,
        decision: str,
        decided_by: str | None = None,
        decided_at: str | None = None,
    ) -> None:
        self._mock_approval_data.append(
            {
                "po_number": po_number,
                "decision": decision,
                "decided_by": decided_by or "",
                "decided_at": decided_at or "",
            }
        )

    def add_mock_inventory_item(
        self,
        sku: str,
        description: str,
        quantity_on_hand: int,
        reorder_point: int,
        max_stock_level: int,
        unit_cost: float,
        status: str = "normal",
    ) -> None:
        self._mock_inventory_data.append(
            {
                "sku": sku,
                "description": description,
                "quantity_on_hand": quantity_on_hand,
                "reorder_point": reorder_point,
                "max_stock_level": max_stock_level,
                "unit_cost": unit_cost,
                "status": status,
            }
        )

    def clear_data(self) -> None:
        self._mock_po_data = []
        self._mock_approval_data = []
        self._mock_inventory_data = []

    # ------------------------------------------------------------------
    # Report generators
    # ------------------------------------------------------------------

    def generate_po_processing_report(self) -> dict[str, Any]:
        pos = self._mock_po_data
        total_pos = len(pos)
        total_value = sum(p.get("total", 0.0) for p in pos)

        by_route: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_vendor: dict[str, int] = {}
        for p in pos:
            by_route[p.get("route", "unknown")] = by_route.get(p.get("route", "unknown"), 0) + 1
            by_type[p.get("po_type", "unknown")] = by_type.get(p.get("po_type", "unknown"), 0) + 1
            by_vendor[p.get("vendor", "unknown")] = by_vendor.get(p.get("vendor", "unknown"), 0) + 1

        return {
            "report_type": "po_processing",
            "total_pos_processed": total_pos,
            "total_value": total_value,
            "avg_po_value": total_value / total_pos if total_pos > 0 else 0.0,
            "pos_by_route": by_route,
            "pos_by_type": by_type,
            "top_vendors": sorted(by_vendor.items(), key=lambda x: -x[1])[:5],
        }

    def generate_approval_stats_report(self) -> dict[str, Any]:
        approvals = self._mock_approval_data
        decision_counts: dict[str, int] = {}
        for a in approvals:
            d = a.get("decision", "unknown")
            decision_counts[d] = decision_counts.get(d, 0) + 1

        total = len(approvals)
        approved = decision_counts.get("approved", 0)
        rejected = decision_counts.get("rejected", 0)
        expired = decision_counts.get("expired", 0)
        pending = decision_counts.get("pending", 0)

        decided = approved + rejected
        approval_rate = approved / decided if decided > 0 else 0.0
        avg_time_hours = 4.0

        # Operational status
        if total == 0:
            status = "healthy"
        elif approval_rate > 0.7:
            status = "healthy"
        elif approval_rate > 0.5:
            status = "warning"
        else:
            status = "critical"

        return {
            "report_type": "approval_stats",
            "total_decisions": total,
            "approved_count": approved,
            "rejected_count": rejected,
            "expired_count": expired,
            "pending_count": pending,
            "approval_rate": approval_rate,
            "avg_decision_time_hours": avg_time_hours,
            "decision_breakdown": decision_counts,
            "status": status,
        }

    def generate_inventory_alerts_report(self) -> dict[str, Any]:
        items = self._mock_inventory_data
        total_items = len(items)
        total_value = sum(
            item.get("quantity_on_hand", 0) * item.get("unit_cost", 0.0) for item in items
        )

        status_counts: dict[str, int] = {}
        for item in items:
            status = item.get("status", "normal")
            status_counts[status] = status_counts.get(status, 0) + 1

        low_stock = status_counts.get("low_stock", 0)
        out_of_stock = status_counts.get("out_of_stock", 0)
        overstock = status_counts.get("overstock", 0)
        normal = status_counts.get("normal", 0)

        low_stock_skus = [item["sku"] for item in items if item.get("status") == "low_stock"]
        out_of_stock_skus = [item["sku"] for item in items if item.get("status") == "out_of_stock"]
        overstock_skus = [item["sku"] for item in items if item.get("status") == "overstock"]

        total_alerts = low_stock + out_of_stock + overstock
        health = 100 - out_of_stock * 15 - low_stock * 5 - overstock * 3
        health = max(0, min(100, health))

        return {
            "report_type": "inventory_alerts",
            "total_items_monitored": total_items,
            "total_inventory_value": total_value,
            "status_breakdown": status_counts,
            "low_stock_count": low_stock,
            "out_of_stock_count": out_of_stock,
            "overstock_count": overstock,
            "normal_count": normal,
            "low_stock_skus": low_stock_skus,
            "out_of_stock_skus": out_of_stock_skus,
            "overstock_skus": overstock_skus,
            "total_alerts": total_alerts,
            "health_score": health,
        }

    def generate_daily_summary(self) -> dict[str, Any]:
        po_report = self.generate_po_processing_report()
        approval_report = self.generate_approval_stats_report()
        inventory_report = self.generate_inventory_alerts_report()

        insights: list[str] = []
        warnings: list[str] = []

        if inventory_report["out_of_stock_count"] > 0:
            warnings.append(
                f"CRITICAL: {inventory_report['out_of_stock_count']} item(s) out of stock: "
                + ", ".join(inventory_report["out_of_stock_skus"])
            )

        if inventory_report["low_stock_count"] > 0:
            insights.append(
                f"ATTENTION: {inventory_report['low_stock_count']} item(s) low stock: "
                + ", ".join(inventory_report["low_stock_skus"])
            )

        if approval_report["total_decisions"] > 0 and approval_report["approval_rate"] < 0.7:
            warnings.append(
                f"WARNING: Approval rate is {approval_report['approval_rate']:.0%} "
                f"({approval_report['approved_count']}/{approval_report['approved_count'] + approval_report['rejected_count']} approved)"
            )

        if po_report["total_pos_processed"] == 0:
            insights.append("No POs processed today — check inbound email connector")

        health_score = inventory_report["health_score"]
        if approval_report["status"] == "critical":
            health_score = min(health_score, 50)
        elif approval_report["status"] == "warning":
            health_score = min(health_score, 75)

        return {
            "report_type": "daily_summary",
            "period": "daily",
            "generated_at": "",
            "po_processing": po_report,
            "approval_stats": approval_report,
            "inventory_alerts": inventory_report,
            "overall_health_score": health_score,
            "insights": insights,
            "warnings": warnings,
        }

    def generate_full_dashboard(self) -> dict[str, Any]:
        daily = self.generate_daily_summary()
        return {
            "report_type": "full_dashboard",
            "period": "daily",
            "generated_at": "",
            "overall_health_score": daily["overall_health_score"],
            "po_metrics": daily["po_processing"],
            "approval_metrics": daily["approval_stats"],
            "inventory_metrics": daily["inventory_alerts"],
            "alerts_summary": {
                "total_alerts": daily["inventory_alerts"]["total_alerts"],
                "critical_alerts": daily["inventory_alerts"]["out_of_stock_count"],
                "warning_alerts": daily["inventory_alerts"]["low_stock_count"]
                + daily["inventory_alerts"]["overstock_count"],
            },
            "insights": daily["insights"],
            "warnings": daily["warnings"],
        }

    # ------------------------------------------------------------------
    # Agent handle
    # ------------------------------------------------------------------

    async def handle(self, request: TaskRequest) -> AgentResponse:
        action = request.action

        if action == "generate_report":
            report_type = request.payload.get("report_type", "daily_summary")
            return await self._generate_report(request, report_type)

        elif action == "get_dashboard":
            dashboard = self.generate_full_dashboard()
            dashboard["generated_at"] = request.payload.get("generated_at", "")
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.SUCCESS,
                result={"dashboard": dashboard},
            )

        elif action == "get_po_report":
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.SUCCESS,
                result={"report": self.generate_po_processing_report()},
            )

        elif action == "get_approval_report":
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.SUCCESS,
                result={"report": self.generate_approval_stats_report()},
            )

        elif action == "get_inventory_report":
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.SUCCESS,
                result={"report": self.generate_inventory_alerts_report()},
            )

        else:
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"Unsupported action: {action!r}",
                ),
            )

    async def _generate_report(self, request: TaskRequest, report_type: str) -> AgentResponse:
        if report_type == "daily_summary":
            report = self.generate_daily_summary()
        elif report_type == "po_processing":
            report = self.generate_po_processing_report()
        elif report_type == "approval_stats":
            report = self.generate_approval_stats_report()
        elif report_type == "inventory_alerts":
            report = self.generate_inventory_alerts_report()
        elif report_type == "full_dashboard":
            report = self.generate_full_dashboard()
        else:
            return AgentResponse(
                task_id=request.task_id,
                agent="supply_chain_reporter-v1",
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"Unknown report type: {report_type!r}",
                ),
            )

        report["generated_at"] = request.payload.get("generated_at", "")
        return AgentResponse(
            task_id=request.task_id,
            agent="supply_chain_reporter-v1",
            status=AgentResponseStatus.SUCCESS,
            result={"report": report},
        )


# ---------------------------------------------------------------------------
# Factory for registry
# ---------------------------------------------------------------------------

SUPPLY_CHAIN_REPORTING_CAPABILITIES = frozenset(
    {
        "supply_chain.generate_report",
        "supply_chain.get_dashboard",
        "supply_chain.get_po_report",
        "supply_chain.get_approval_report",
        "supply_chain.get_inventory_report",
    }
)


def create_supply_chain_reporter(llm=None, settings=None) -> SupplyChainReporter:
    return SupplyChainReporter()
