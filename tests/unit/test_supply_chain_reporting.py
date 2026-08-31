"""Unit tests for supply_chain reporting agent (Phase SC).

Validates the SupplyChainReporter's ability to generate PO processing
reports, approval statistics, inventory alerts reports, and daily summaries.
"""

from __future__ import annotations

import pytest

from agents.supply_chain.reporting import (
    SupplyChainReporter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reporter() -> SupplyChainReporter:
    """Fresh SupplyChainReporter instance for isolated tests."""
    return SupplyChainReporter()


@pytest.fixture
def reporter_with_mock_data(reporter: SupplyChainReporter) -> SupplyChainReporter:
    """Reporter pre-loaded with mock PO, approval, and inventory data."""
    # Add mock POs
    reporter.add_mock_po("PO-2024-001", "Acme Corp", 1000.0, "auto_approved", "new")
    reporter.add_mock_po(
        "PO-2024-002", "Big Vendor", 5000.0, "approval_required_manager_a", "reorder"
    )
    reporter.add_mock_po(
        "PO-2024-003", "Huge Vendor", 15000.0, "approval_required_manager_b", "new"
    )
    reporter.add_mock_po("PO-2024-004", "Acme Corp", 200.0, "auto_approved", "exchange")
    reporter.add_mock_po("PO-2024-005", "Small Vendor", 50.0, "auto_approved", "new")

    # Add mock approval decisions
    reporter.add_mock_approval("PO-2024-002", "approved", "manager_a")
    reporter.add_mock_approval("PO-2024-003", "approved", "manager_b")
    reporter.add_mock_approval("PO-2024-006", "rejected", "manager_a")
    reporter.add_mock_approval("PO-2024-007", "expired")
    reporter.add_mock_approval("PO-2024-008", "pending")

    # Add mock inventory items
    reporter.add_mock_inventory_item("SKU-001", "Widget A", 100, 20, 150, 5.0, "normal")
    reporter.add_mock_inventory_item("SKU-002", "Widget B", 15, 20, 100, 10.0, "low_stock")
    reporter.add_mock_inventory_item("SKU-003", "Widget C", 0, 10, 50, 20.0, "out_of_stock")
    reporter.add_mock_inventory_item("SKU-004", "Widget D", 300, 50, 200, 2.0, "overstock")
    reporter.add_mock_inventory_item("SKU-005", "Widget E", 25, 30, 100, 15.0, "low_stock")

    return reporter


# ---------------------------------------------------------------------------
# Basic initialization tests
# ---------------------------------------------------------------------------


def test_reporter_initialized_empty(reporter):
    """Reporter starts with empty data."""
    assert len(reporter._mock_po_data) == 0
    assert len(reporter._mock_approval_data) == 0
    assert len(reporter._mock_inventory_data) == 0


def test_reporter_add_mock_po(reporter):
    """Adding a mock PO stores it correctly."""
    reporter.add_mock_po("PO-2024-100", "Test Vendor", 500.0, "auto_approved", "new")

    assert len(reporter._mock_po_data) == 1
    po = reporter._mock_po_data[0]
    assert po["po_number"] == "PO-2024-100"
    assert po["vendor"] == "Test Vendor"
    assert po["total"] == 500.0
    assert po["route"] == "auto_approved"
    assert po["po_type"] == "new"


def test_reporter_add_mock_approval(reporter):
    """Adding a mock approval stores it correctly."""
    reporter.add_mock_approval("PO-2024-200", "approved", "manager_a")

    assert len(reporter._mock_approval_data) == 1
    app = reporter._mock_approval_data[0]
    assert app["po_number"] == "PO-2024-200"
    assert app["decision"] == "approved"
    assert app["decided_by"] == "manager_a"


def test_reporter_add_mock_inventory_item(reporter):
    """Adding a mock inventory item stores it correctly."""
    reporter.add_mock_inventory_item("SKU-123", "Test Item", 50, 20, 100, 10.0, "normal")

    assert len(reporter._mock_inventory_data) == 1
    item = reporter._mock_inventory_data[0]
    assert item["sku"] == "SKU-123"
    assert item["description"] == "Test Item"
    assert item["quantity_on_hand"] == 50
    assert item["status"] == "normal"


def test_reporter_clear_data(reporter_with_mock_data):
    """clear_data removes all mock data."""
    reporter_with_mock_data.clear_data()

    assert len(reporter_with_mock_data._mock_po_data) == 0
    assert len(reporter_with_mock_data._mock_approval_data) == 0
    assert len(reporter_with_mock_data._mock_inventory_data) == 0


# ---------------------------------------------------------------------------
# PO Processing Report tests
# ---------------------------------------------------------------------------


def test_generate_po_processing_report_empty(reporter):
    """Empty reporter returns zero metrics."""
    report = reporter.generate_po_processing_report()

    assert report["total_pos_processed"] == 0
    assert report["total_value"] == 0.0
    assert report["avg_po_value"] == 0.0
    assert report["pos_by_route"] == {}
    assert report["pos_by_type"] == {}


def test_generate_po_processing_report_with_data(reporter_with_mock_data):
    """Report with mock data returns correct metrics."""
    report = reporter_with_mock_data.generate_po_processing_report()

    assert report["total_pos_processed"] == 5
    assert report["total_value"] == pytest.approx(21250.0)  # 1000+5000+15000+200+50
    assert report["avg_po_value"] == pytest.approx(4250.0)  # 21250/5

    # Check route breakdown
    assert report["pos_by_route"]["auto_approved"] == 3
    assert report["pos_by_route"]["approval_required_manager_a"] == 1
    assert report["pos_by_route"]["approval_required_manager_b"] == 1

    # Check type breakdown
    assert report["pos_by_type"]["new"] == 3
    assert report["pos_by_type"]["reorder"] == 1
    assert report["pos_by_type"]["exchange"] == 1

    # Check top vendors
    top_vendors = report["top_vendors"]
    assert len(top_vendors) == 4  # All 4 vendors appear
    assert top_vendors[0][0] == "Acme Corp"  # 2 POs
    assert top_vendors[0][1] == 2


# ---------------------------------------------------------------------------
# Approval Stats Report tests
# ---------------------------------------------------------------------------


def test_generate_approval_stats_report_empty(reporter):
    """Empty reporter returns zero approval metrics."""
    report = reporter.generate_approval_stats_report()

    assert report["total_decisions"] == 0
    assert report["approved_count"] == 0
    assert report["rejected_count"] == 0
    assert report["expired_count"] == 0
    assert report["pending_count"] == 0
    assert report["approval_rate"] == 0.0


def test_generate_approval_stats_report_with_data(reporter_with_mock_data):
    """Report with mock data returns correct approval metrics."""
    report = reporter_with_mock_data.generate_approval_stats_report()

    assert report["total_decisions"] == 5
    assert report["approved_count"] == 2
    assert report["rejected_count"] == 1
    assert report["expired_count"] == 1
    assert report["pending_count"] == 1
    # Approval rate: 2 / (2+1) = 2/3 ≈ 0.667
    assert report["approval_rate"] == pytest.approx(0.6667, rel=0.01)
    assert report["status"] == "warning"  # < 70%


def test_generate_approval_stats_report_healthy(reporter: SupplyChainReporter):
    """High approval rate produces healthy status."""
    reporter.add_mock_approval("PO-1", "approved")
    reporter.add_mock_approval("PO-2", "approved")
    reporter.add_mock_approval("PO-3", "approved")
    reporter.add_mock_approval("PO-4", "rejected")

    report = reporter.generate_approval_stats_report()
    assert report["approved_count"] == 3
    assert report["rejected_count"] == 1
    assert report["approval_rate"] == pytest.approx(0.75)
    assert report["status"] == "healthy"  # >= 70%


def test_generate_approval_stats_report_critical(reporter: SupplyChainReporter):
    """Low approval rate produces critical status."""
    reporter.add_mock_approval("PO-1", "approved")
    reporter.add_mock_approval("PO-2", "rejected")
    reporter.add_mock_approval("PO-3", "rejected")
    reporter.add_mock_approval("PO-4", "rejected")

    report = reporter.generate_approval_stats_report()
    assert report["approved_count"] == 1
    assert report["rejected_count"] == 3
    assert report["approval_rate"] == pytest.approx(0.25)
    assert report["status"] == "critical"  # < 50%


# ---------------------------------------------------------------------------
# Inventory Alerts Report tests
# ---------------------------------------------------------------------------


def test_generate_inventory_alerts_report_empty(reporter):
    """Empty reporter returns zero inventory metrics."""
    report = reporter.generate_inventory_alerts_report()

    assert report["total_items_monitored"] == 0
    assert report["total_inventory_value"] == 0.0
    assert report["status_breakdown"] == {}
    assert report["low_stock_count"] == 0
    assert report["out_of_stock_count"] == 0
    assert report["overstock_count"] == 0
    assert report["normal_count"] == 0
    assert report["health_score"] == 100


def test_generate_inventory_alerts_report_with_data(reporter_with_mock_data):
    """Report with mock data returns correct inventory metrics."""
    report = reporter_with_mock_data.generate_inventory_alerts_report()

    assert report["total_items_monitored"] == 5
    # total_value: 100*5 + 15*10 + 0*20 + 300*2 + 25*15 = 500+150+0+600+375 = 1625
    assert report["total_inventory_value"] == pytest.approx(1625.0)
    assert report["status_breakdown"]["normal"] == 1
    assert report["status_breakdown"]["low_stock"] == 2
    assert report["status_breakdown"]["out_of_stock"] == 1
    assert report["status_breakdown"]["overstock"] == 1
    assert report["low_stock_count"] == 2
    assert report["out_of_stock_count"] == 1
    assert report["overstock_count"] == 1
    assert report["normal_count"] == 1
    assert report["total_alerts"] == 4
    assert report["health_score"] == pytest.approx(100 - 15 - 10 - 3)  # 72

    # Check SKU lists
    assert "SKU-002" in report["low_stock_skus"]
    assert "SKU-005" in report["low_stock_skus"]
    assert "SKU-003" in report["out_of_stock_skus"]
    assert "SKU-004" in report["overstock_skus"]


# ---------------------------------------------------------------------------
# Daily Summary tests
# ---------------------------------------------------------------------------


def test_generate_daily_summary_with_data(reporter_with_mock_data):
    """Daily summary consolidates all report types."""
    summary = reporter_with_mock_data.generate_daily_summary()

    assert summary["report_type"] == "daily_summary"
    assert summary["period"] == "daily"

    # PO processing
    assert summary["po_processing"]["total_pos_processed"] == 5

    # Approval stats
    assert summary["approval_stats"]["total_decisions"] == 5

    # Inventory alerts
    assert summary["inventory_alerts"]["total_items_monitored"] == 5

    # Warnings (out of stock SKU-003)
    assert len(summary["warnings"]) > 0
    assert any("out of stock" in w.lower() for w in summary["warnings"])

    # Insights (low stock items)
    assert len(summary["insights"]) > 0
    assert any("low stock" in i.lower() for i in summary["insights"])


def test_generate_daily_summary_empty(reporter):
    """Empty reporter generates summary with no warnings (but may have operational insights)."""
    summary = reporter.generate_daily_summary()

    assert summary["po_processing"]["total_pos_processed"] == 0
    assert summary["approval_stats"]["total_decisions"] == 0
    assert summary["inventory_alerts"]["total_items_monitored"] == 0
    assert summary["warnings"] == []
    # The reporter may flag no POs processed as an operational insight
    assert summary["overall_health_score"] == 100


# ---------------------------------------------------------------------------
# Full Dashboard tests
# ---------------------------------------------------------------------------


def test_generate_full_dashboard_with_data(reporter_with_mock_data):
    """Full dashboard contains all sections."""
    dashboard = reporter_with_mock_data.generate_full_dashboard()

    assert dashboard["report_type"] == "full_dashboard"
    assert dashboard["period"] == "daily"
    assert "overall_health_score" in dashboard
    assert "po_metrics" in dashboard
    assert "approval_metrics" in dashboard
    assert "inventory_metrics" in dashboard
    assert "alerts_summary" in dashboard
    assert "insights" in dashboard
    assert "warnings" in dashboard

    assert dashboard["po_metrics"]["total_pos_processed"] == 5
    assert dashboard["approval_metrics"]["total_decisions"] == 5
    assert dashboard["inventory_metrics"]["total_items_monitored"] == 5


# ---------------------------------------------------------------------------
# Agent handle() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_get_dashboard(reporter_with_mock_data):
    """get_dashboard action returns full dashboard."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_dashboard",
        payload={"generated_at": "2024-01-15T00:00:00Z"},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "dashboard" in response.result
    dashboard = response.result["dashboard"]
    assert dashboard["report_type"] == "full_dashboard"
    assert dashboard["overall_health_score"] > 0


@pytest.mark.asyncio
async def test_handle_get_po_report(reporter_with_mock_data):
    """get_po_report action returns PO processing report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_po_report",
        payload={},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_pos_processed"] == 5


@pytest.mark.asyncio
async def test_handle_get_approval_report(reporter_with_mock_data):
    """get_approval_report action returns approval stats report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_approval_report",
        payload={},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_decisions"] == 5


@pytest.mark.asyncio
async def test_handle_get_inventory_report(reporter_with_mock_data):
    """get_inventory_report action returns inventory alerts report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_inventory_report",
        payload={},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_items_monitored"] == 5


@pytest.mark.asyncio
async def test_handle_generate_daily_summary_report(reporter_with_mock_data):
    """generate_report with daily_summary returns daily summary."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="generate_report",
        payload={"report_type": "daily_summary", "generated_at": "2024-01-15"},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["report_type"] == "daily_summary"
    assert response.result["report"]["overall_health_score"] > 0


@pytest.mark.asyncio
async def test_handle_generate_po_processing_report(reporter_with_mock_data):
    """generate_report with po_processing returns PO report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="generate_report",
        payload={"report_type": "po_processing"},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_pos_processed"] == 5


@pytest.mark.asyncio
async def test_handle_generate_approval_stats_report(reporter_with_mock_data):
    """generate_report with approval_stats returns approval report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="generate_report",
        payload={"report_type": "approval_stats"},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_decisions"] == 5


@pytest.mark.asyncio
async def test_handle_generate_inventory_alerts_report(reporter_with_mock_data):
    """generate_report with inventory_alerts returns inventory report."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="generate_report",
        payload={"report_type": "inventory_alerts"},
        context=TaskContext(),
    )

    response = await reporter_with_mock_data.handle(request)

    assert response.status == "success"
    assert "report" in response.result
    assert response.result["report"]["total_items_monitored"] == 5


@pytest.mark.asyncio
async def test_handle_unsupported_action(reporter):
    """Unsupported actions are rejected."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="unknown_action",
        payload={},
        context=TaskContext(),
    )

    response = await reporter.handle(request)

    assert response.status == "rejected"
    assert response.error is not None
    assert "Unsupported action" in response.error.message


@pytest.mark.asyncio
async def test_handle_unknown_report_type(reporter):
    """Unknown report types are rejected."""
    from uuid import uuid4

    from packages.contracts.enums import Domain
    from packages.contracts.models import TaskContext, TaskRequest

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="generate_report",
        payload={"report_type": "unknown_type"},
        context=TaskContext(),
    )

    response = await reporter.handle(request)

    assert response.status == "rejected"
    assert response.error is not None
    assert "Unknown report type" in response.error.message


# ---------------------------------------------------------------------------
# Factory function test
# ---------------------------------------------------------------------------


def test_create_supply_chain_reporter():
    """create_supply_chain_reporter returns SupplyChainReporter instance."""
    from agents.supply_chain.reporting import create_supply_chain_reporter

    reporter = create_supply_chain_reporter(llm=None, settings=None)
    assert isinstance(reporter, SupplyChainReporter)
