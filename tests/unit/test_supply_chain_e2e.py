# -*- coding: utf-8 -*-
"""End-to-end pipeline tests for Supply Chain Automation (Phase SC).

Validates the complete supply chain flow:
  inbound email → PO Agent → Approval Workflow → Inventory Monitor → Reporting Agent
"""

from __future__ import annotations

import pytest

from agents.supply_chain.inbound import process_inbound_email
from agents.supply_chain.po_agent import PurchaseOrderAgent
from agents.supply_chain.approval import ApprovalWorkflow, ApprovalState, needs_approval
from agents.supply_chain.inventory import (
    InventoryMonitor,
    InventoryItem,
    InventoryStatus,
)
from agents.supply_chain.reporting import SupplyChainReporter
from packages.config.settings import Settings
from packages.contracts.enums import Domain
from packages.contracts.models import AgentDescriptor, TaskRequest, TaskContext
from packages.llm.mock import MockLLMProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def po_agent() -> PurchaseOrderAgent:
    """PurchaseOrderAgent with mock LLM (rule-based fallback)."""
    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}
    return PurchaseOrderAgent(llm=MockLLMProvider(), settings=settings)


@pytest.fixture
def approval_workflow_factory() -> callable:
    """Factory function to create ApprovalWorkflow instances."""

    def _create(po_data: dict, approver_email: str = "manager@example.com") -> ApprovalWorkflow:
        return ApprovalWorkflow(po_data=po_data, approver_email=approver_email)

    return _create


@pytest.fixture
def inventory_monitor() -> InventoryMonitor:
    """Fresh InventoryMonitor instance."""
    return InventoryMonitor()


@pytest.fixture
def reporting_agent() -> SupplyChainReporter:
    """Fresh SupplyChainReporter instance."""
    return SupplyChainReporter()


# ---------------------------------------------------------------------------
# E2E Test 1: Inbound email → PO Agent → successful PO processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_inbound_to_po_agent(po_agent):
    """Inbound email is parsed by PO Agent into structured PO data."""
    email_content = (
        "PO NUMBER: PO-2024-E2E-001\n"
        "VENDOR: Acme Supply Co\n"
        "Vendor Email: vendor@acmesupply.com\n"
        "Date: 2024-09-15\n"
        "\n"
        "Items:\n"
        "- SKU-E2E-001, Industrial Widget, QTY: 100 @ $25.00 = $2500.00\n"
        "- SKU-E2E-002, Precision Sensor, QTY: 50 @ $100.00 = $5000.00\n"
        "\n"
        "TOTAL: $7500.00\n"
    )

    resp = await process_inbound_email(email_content, po_agent=po_agent)

    assert resp.status.value == "success"
    po = resp.result["po"]
    assert po["po_number"] == "PO-2024-E2E-001"
    assert po["vendor"] == "Acme Supply Co"
    assert po["vendor_email"] == "vendor@acmesupply.com"
    assert len(po["items"]) == 2
    assert po["total"] == 7500.0
    assert po["route"] == "approval_required_manager_b"  # > 5000 threshold


# ---------------------------------------------------------------------------
# E2E Test 2: PO with auto-approval (no human approval needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_auto_approved_po():
    """Small PO bypasses approval workflow and is auto-approved."""
    email_content = (
        "PO NUMBER: PO-2024-E2E-002\n"
        "VENDOR: Small Vendor Inc\n"
        "Items:\n"
        "- SKU-SML-001, Small Part, QTY: 10 @ $5.00 = $50.00\n"
        "TOTAL: $50.00\n"
    )

    resp = await process_inbound_email(email_content)

    assert resp.status.value == "success"
    po = resp.result["po"]
    assert po["route"] == "auto_approved"
    assert po["po_type"] == "new"
    assert po["total"] == 50.0

    # Verify needs_approval returns False
    assert needs_approval(po) is False


# ---------------------------------------------------------------------------
# E2E Test 3: PO requiring approval → workflow transitions correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_po_requires_approval():
    """PO above threshold triggers approval workflow."""
    from packages.llm.mock import MockLLMProvider

    # Script LLM for structured PO parsing
    llm = MockLLMProvider()
    llm.script({
        "po_number": "PO-2024-E2E-003",
        "vendor": "Big Manufacturer",
        "vendor_email": "orders@bigmanufacturer.com",
        "date": "2024-09-16",
        "items": [
            {"sku": "SKU-BIG-001", "description": "Heavy Machinery Part", "quantity": 20, "unit_price": 300.0, "total_price": 6000.0},
        ],
        "total": 6000.0,
    })
    llm.script("new")

    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}
    po_agent = PurchaseOrderAgent(llm=llm, settings=settings)

    email_content = (
        "PO NUMBER: PO-2024-E2E-003\n"
        "VENDOR: Big Manufacturer\n"
        "Items:\n"
        "- SKU-BIG-001, Heavy Machinery Part, QTY: 20 @ $300.00 = $6000.00\n"
        "TOTAL: $6000.00\n"
    )

    resp = await process_inbound_email(email_content, po_agent=po_agent)

    assert resp.status.value == "success"
    po = resp.result["po"]
    assert po["route"] == "approval_required_manager_b"
    assert needs_approval(po) is True

    # Create approval workflow and verify it transitions to PENDING_HUMAN_APPROVAL
    workflow = ApprovalWorkflow(po_data=po, approver_email="manager@bigcorp.com")
    assert workflow.state == ApprovalState.PENDING

    # Simulate human approval — must be in PENDING_HUMAN_APPROVAL state first
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL
    approve_resp = await workflow.resolve(decision="approved", decided_by="plant_manager_john")
    assert approve_resp.status.value == "success"
    assert workflow.state == ApprovalState.APPROVED


# ---------------------------------------------------------------------------
# E2E Test 4: Rejected PO → workflow handles rejection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_po_rejected():
    """Human rejects a PO requiring approval."""
    from packages.llm.mock import MockLLMProvider

    # Script LLM for structured PO parsing
    llm = MockLLMProvider()
    llm.script({
        "po_number": "PO-2024-E2E-004",
        "vendor": "Questionable Supplier",
        "vendor_email": "sales@quesupplier.com",
        "date": "2024-09-16",
        "items": [
            {"sku": "SKU-QUES-001", "description": "Unverified Component", "quantity": 5, "unit_price": 2000.0, "total_price": 10000.0},
        ],
        "total": 10000.0,
    })
    llm.script("new")

    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}
    po_agent = PurchaseOrderAgent(llm=llm, settings=settings)

    email_content = (
        "PO NUMBER: PO-2024-E2E-004\n"
        "VENDOR: Questionable Supplier\n"
        "Items:\n"
        "- SKU-QUES-001, Unverified Component, QTY: 5 @ $2000.00 = $10000.00\n"
        "TOTAL: $10000.00\n"
    )

    resp = await process_inbound_email(email_content, po_agent=po_agent)
    po = resp.result["po"]

    workflow = ApprovalWorkflow(po_data=po, approver_email="qa_manager@company.com")
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL
    reject_resp = await workflow.resolve(decision="rejected", decided_by="quality_manager_sara")

    assert reject_resp.status.value == "failed"
    assert workflow.state == ApprovalState.REJECTED


# ---------------------------------------------------------------------------
# E2E Test 5: Inventory Monitor processes items and generates alerts
# ---------------------------------------------------------------------------

def test_e2e_inventory_monitoring(inventory_monitor):
    """Inventory Monitor processes items and generates appropriate alerts."""
    items = [
        InventoryItem(sku="INV-E2E-001", description="Available Part", quantity_on_hand=200, reorder_point=50, max_stock_level=150, unit_cost=10.0),
        InventoryItem(sku="INV-E2E-002", description="Low Stock Item", quantity_on_hand=15, reorder_point=20, max_stock_level=100, unit_cost=25.0),
        InventoryItem(sku="INV-E2E-003", description="Out of Stock Item", quantity_on_hand=0, reorder_point=10, max_stock_level=50, unit_cost=50.0),
        InventoryItem(sku="INV-E2E-004", description="Overstock Item", quantity_on_hand=500, reorder_point=30, max_stock_level=200, unit_cost=5.0),
    ]

    for item in items:
        inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    # Only items with threshold violations generate alerts
    # INV-E2E-001: overstock (200 >= 150) -> alert
    # INV-E2E-002: low stock (15 <= 20) -> alert
    # INV-E2E-003: out of stock -> alert
    # INV-E2E-004: overstock (500 >= 200) -> alert
    assert len(alerts) == 4

    # Check specific alerts
    oos_alert = next((a for a in alerts if a.alert_type.value == "out_of_stock"), None)
    assert oos_alert is not None
    assert oos_alert.sku == "INV-E2E-003"
    assert oos_alert.severity == "critical"

    low_alert = next((a for a in alerts if a.alert_type.value == "low_stock"), None)
    assert low_alert is not None
    assert low_alert.sku == "INV-E2E-002"
    assert low_alert.severity == "warning"

    overstock_alert = next((a for a in alerts if a.alert_type.value == "overstock"), None)
    assert overstock_alert is not None
    # Order may vary; check by SKU
    assert overstock_alert.sku in ("INV-E2E-001", "INV-E2E-004")
    assert overstock_alert.severity == "warning"

    # Summary (both INV-E2E-001 and INV-E2E-004 are overstock)
    summary = inventory_monitor.get_summary()
    assert summary["total_items"] == 4
    assert summary["out_of_stock_count"] == 1
    assert summary["low_stock_count"] == 1
    assert summary["overstock_count"] == 2  # Both INV-E2E-001 (200 >= 150) and INV-E2E-004 (500 >= 200)
    assert summary["normal_count"] == 0


# ---------------------------------------------------------------------------
# E2E Test 6: Reporting Agent generates complete dashboard
# ---------------------------------------------------------------------------

def test_e2e_reporting_dashboard(reporting_agent):
    """Reporting Agent consolidates PO, approval, and inventory data into dashboard."""
    # Add mock PO data
    reporting_agent.add_mock_po("PO-E2E-001", "Supplier A", 1000.0, "auto_approved", "new")
    reporting_agent.add_mock_po("PO-E2E-002", "Supplier B", 5000.0, "approval_required_manager_a", "reorder")
    reporting_agent.add_mock_po("PO-E2E-003", "Supplier C", 15000.0, "approval_required_manager_b", "new")

    # Add mock approval data
    reporting_agent.add_mock_approval("PO-E2E-002", "approved", "manager_alice")
    reporting_agent.add_mock_approval("PO-E2E-003", "approved", "manager_bob")
    reporting_agent.add_mock_approval("PO-E2E-004", "rejected", "manager_charlie")
    reporting_agent.add_mock_approval("PO-E2E-005", "pending")

    # Add mock inventory data
    reporting_agent.add_mock_inventory_item("SKU-RPT-001", "Component X", 150, 50, 200, 20.0, "normal")
    reporting_agent.add_mock_inventory_item("SKU-RPT-002", "Component Y", 10, 20, 100, 35.0, "low_stock")
    reporting_agent.add_mock_inventory_item("SKU-RPT-003", "Component Z", 0, 10, 50, 75.0, "out_of_stock")
    reporting_agent.add_mock_inventory_item("SKU-RPT-004", "Component W", 400, 30, 200, 8.0, "overstock")

    # Generate dashboard
    dashboard = reporting_agent.generate_full_dashboard()

    assert dashboard["report_type"] == "full_dashboard"
    assert dashboard["period"] == "daily"
    assert "overall_health_score" in dashboard
    assert "po_metrics" in dashboard
    assert "approval_metrics" in dashboard
    assert "inventory_metrics" in dashboard
    assert "alerts_summary" in dashboard

    # PO metrics
    assert dashboard["po_metrics"]["total_pos_processed"] == 3
    assert dashboard["po_metrics"]["total_value"] == pytest.approx(21000.0)

    # Approval metrics
    assert dashboard["approval_metrics"]["total_decisions"] == 4
    assert dashboard["approval_metrics"]["approved_count"] == 2
    assert dashboard["approval_metrics"]["rejected_count"] == 1
    assert dashboard["approval_metrics"]["pending_count"] == 1

    # Inventory metrics
    assert dashboard["inventory_metrics"]["total_items_monitored"] == 4
    assert dashboard["inventory_metrics"]["low_stock_count"] == 1
    assert dashboard["inventory_metrics"]["out_of_stock_count"] == 1
    assert dashboard["inventory_metrics"]["overstock_count"] == 1

    # Alerts summary
    assert dashboard["alerts_summary"]["total_alerts"] == 3
    assert dashboard["alerts_summary"]["critical_alerts"] == 1
    assert dashboard["alerts_summary"]["warning_alerts"] == 2


# ---------------------------------------------------------------------------
# E2E Test 7: Reporting Agent handle() methods work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_reporting_get_dashboard(reporting_agent):
    """get_dashboard action returns full dashboard."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext

    reporting_agent.add_mock_po("PO-001", "Vendor A", 1000.0, "auto_approved", "new")
    reporting_agent.add_mock_inventory_item("SKU-001", "Part A", 100, 20, 150, 10.0, "normal")

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_dashboard",
        payload={"generated_at": "2024-09-16T10:00:00Z"},
        context=TaskContext(),
    )

    response = await reporting_agent.handle(request)

    assert response.status == "success"
    dashboard = response.result["dashboard"]
    assert dashboard["report_type"] == "full_dashboard"


@pytest.mark.asyncio
async def test_e2e_reporting_get_po_report(reporting_agent):
    """get_po_report action returns PO processing report."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext

    reporting_agent.add_mock_po("PO-100", "Vendor X", 500.0, "auto_approved", "new")

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_po_report",
        payload={},
        context=TaskContext(),
    )

    response = await reporting_agent.handle(request)

    assert response.status == "success"
    report = response.result["report"]
    assert report["total_pos_processed"] == 1
    assert report["total_value"] == 500.0


@pytest.mark.asyncio
async def test_e2e_reporting_get_inventory_report(reporting_agent):
    """get_inventory_report action returns inventory alerts report."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext

    reporting_agent.add_mock_inventory_item("SKU-INV-001", "Item", 0, 10, 50, 50.0, "out_of_stock")
    reporting_agent.add_mock_inventory_item("SKU-INV-002", "Item 2", 25, 20, 100, 15.0, "low_stock")

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_inventory_report",
        payload={},
        context=TaskContext(),
    )

    response = await reporting_agent.handle(request)

    assert response.status == "success"
    report = response.result["report"]
    assert report["total_items_monitored"] == 2
    assert report["out_of_stock_count"] == 1
    assert report["low_stock_count"] == 1


# ---------------------------------------------------------------------------
# E2E Test 8: Complete simulated supply chain pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_complete_pipeline():
    """Simulates a complete supply chain workflow from PO receipt to reporting."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.llm.mock import MockLLMProvider
    from packages.config.settings import Settings

    # Initialize all agents with SCRIPTED LLM
    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}

    llm = MockLLMProvider()
    # Script 1: structured PO parse output
    llm.script({
        "po_number": "PO-E2E-FLOW-001",
        "vendor": "Global Parts Supplier",
        "vendor_email": "orders@globalparts.com",
        "date": "2024-09-16",
        "items": [
            {"sku": "SKU-FLOW-001", "description": "Hydraulic Pump", "quantity": 5, "unit_price": 2000.0, "total_price": 10000.0},
            {"sku": "SKU-FLOW-002", "description": "Control Valve", "quantity": 10, "unit_price": 500.0, "total_price": 5000.0},
        ],
        "total": 15000.0,
    })
    # Script 2: classification output
    llm.script("new")

    po_agent = PurchaseOrderAgent(llm=llm, settings=settings)
    inventory_monitor = InventoryMonitor()
    reporting_agent = SupplyChainReporter()

    # Step 1: Inbound email received
    email = (
        "PO NUMBER: PO-E2E-FLOW-001\n"
        "VENDOR: Global Parts Supplier\n"
        "Vendor Email: orders@globalparts.com\n"
        "Date: 2024-09-16\n"
        "\n"
        "Items:\n"
        "- SKU-FLOW-001, Hydraulic Pump, QTY: 5 @ $2000.00 = $10000.00\n"
        "- SKU-FLOW-002, Control Valve, QTY: 10 @ $500.00 = $5000.00\n"
        "TOTAL: $15000.00\n"
    )

    # Step 2: PO Agent parses and routes
    po_resp = await process_inbound_email(email, po_agent=po_agent)
    assert po_resp.status.value == "success"
    po = po_resp.result["po"]
    assert po["po_number"] == "PO-E2E-FLOW-001"
    assert po["route"] == "approval_required_manager_b"
    assert po["total"] == 15000.0

    # Step 3: Approval workflow triggered and resolved
    workflow = ApprovalWorkflow(po_data=po, approver_email="procurement_manager@company.com")
    workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL
    approve_resp = await workflow.resolve(decision="approved", decided_by="procurement_director")

    assert approve_resp.status.value == "success"
    assert workflow.state == ApprovalState.APPROVED

    # Step 4: Inventory monitoring (simulate receiving inventory data for related items)
    inventory_monitor.add_item(InventoryItem(
        sku="SKU-FLOW-001", description="Hydraulic Pump", quantity_on_hand=3, reorder_point=5, max_stock_level=10, unit_cost=2000.0
    ))
    inventory_monitor.add_item(InventoryItem(
        sku="SKU-FLOW-002", description="Control Valve", quantity_on_hand=12, reorder_point=8, max_stock_level=20, unit_cost=500.0
    ))

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 1  # Only SKU-FLOW-001 is low stock (3 <= 5)
    low_alert = alerts[0]
    assert low_alert.sku == "SKU-FLOW-001"
    assert low_alert.alert_type.value == "low_stock"

    # Step 5: Reporting consolidates data
    reporting_agent.add_mock_po(po["po_number"], po["vendor"], po["total"], po["route"], po["po_type"])
    reporting_agent.add_mock_approval(po["po_number"], "approved", "procurement_director")
    reporting_agent.add_mock_inventory_item("SKU-FLOW-001", "Hydraulic Pump", 3, 5, 10, 2000.0, "low_stock")
    reporting_agent.add_mock_inventory_item("SKU-FLOW-002", "Control Valve", 12, 8, 20, 500.0, "normal")

    dashboard = reporting_agent.generate_full_dashboard()

    # Verify: 1 approved PO, 1 low stock item -> health = 100-5=95
    assert dashboard["overall_health_score"] == 95
    assert dashboard["po_metrics"]["total_pos_processed"] == 1
    assert dashboard["approval_metrics"]["approved_count"] == 1
    assert dashboard["inventory_metrics"]["low_stock_count"] == 1

    # Verify health score calculation
    # Base 100, minus 5 for low stock (1 item * 5 points) = 95
    expected_health = 100 - 5  # 1 low stock item * 5 points
    assert dashboard["overall_health_score"] == expected_health
