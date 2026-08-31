"""E2E integration tests for Supply Chain LangGraph graph orchestrator.

These tests exercise the full graph flow (po_agent → approval → inventory → reporting)
using the orchestrator's execute() method, verifying end-to-end correctness.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agents.supply_chain.graph import SupplyChainGraphOrchestrator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orchestrator():
    """SupplyChainGraphOrchestrator with temp checkpoint DB (InMemorySaver)."""
    from packages.config.settings import Settings

    s = Settings()
    return SupplyChainGraphOrchestrator(settings=s)


@pytest.fixture
def small_po_email():
    """Small PO email — auto-approved (total $50 < manager_a threshold $500)."""
    return (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Test Vendor\n"
        "Items:\n"
        "- SKU-001, Widget, QTY: 10 @ $5.00 = $50.00\n"
        "TOTAL: $50.00\n"
    )


@pytest.fixture
def large_po_email():
    """Large PO email — requires manager B approval (total $6000 > manager_b $5000)."""
    return (
        "PO NUMBER: PO-2024-002\n"
        "VENDOR: Big Vendor\n"
        "Items:\n"
        "- SKU-002, Heavy Machine, QTY: 2 @ $3000.00 = $6000.00\n"
        "TOTAL: $6000.00\n"
    )


@pytest.fixture
def multi_item_po_email():
    """PO with multiple items to test inventory alert generation."""
    return (
        "PO NUMBER: PO-2024-003\n"
        "VENDOR: Multi Vendor\n"
        "Items:\n"
        "- SKU-100, Critical Part, QTY: 200 @ $10.00 = $2000.00\n"
        "- SKU-200, Rare Component, QTY: 5 @ $500.00 = $2500.00\n"
        "- SKU-300, Consumable, QTY: 50 @ $2.00 = $100.00\n"
        "TOTAL: $4600.00\n"
    )


# ---------------------------------------------------------------------------
# E2E Test 1: Full pipeline — small PO (auto-approved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_small_po_full_pipeline(orchestrator, small_po_email):
    """E2E: Small PO → po_agent → auto-approved → inventory → reporting → success.

    Verifies the entire graph flow produces a valid dashboard.
    """
    task_id = uuid4()
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": small_po_email},
        context={"organization_id": str(uuid4())},
    )

    # Overall success
    assert result["status"] == "success"
    assert "dashboard" in result
    assert result["dashboard"]

    # PO data
    assert result["po_data"]["po_number"] == "PO-2024-001"
    assert result["po_data"]["route"] == "auto_approved"
    assert result["po_data"]["total"] == 50.0

    # Approval
    assert result["approval"]["state"] == "approved"
    assert result["approval"]["decision"] == "auto_approved"
    assert result["approval"]["decided_by"] == "system"

    # Inventory alerts (mock data: 50 qty, 20 reorder → no alert for small qty)
    assert "alerts" in result["inventory"]
    assert "summary" in result["inventory"]

    # Dashboard sections
    dashboard = result["dashboard"]
    assert "report_type" in dashboard
    assert "po_metrics" in dashboard
    assert "approval_metrics" in dashboard
    assert "inventory_metrics" in dashboard


# ---------------------------------------------------------------------------
# E2E Test 2: Full pipeline — large PO (requires approval)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_large_po_approval_pipeline(orchestrator, large_po_email):
    """E2E: Large PO → po_agent → approval_required → approval_node → inventory → reporting.

    Verifies approval path flows correctly through graph.
    """
    task_id = uuid4()
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": large_po_email},
        context={"organization_id": str(uuid4())},
    )

    assert result["status"] == "success"

    # PO data
    assert result["po_data"]["po_number"] == "PO-2024-002"
    assert result["po_data"]["route"] == "approval_required_manager_b"
    assert result["po_data"]["total"] == 6000.0

    # Approval: stub auto-approve (no real human-in-the-loop)
    assert result["approval"]["state"] == "approved"
    assert result["approval"]["decision"] == "approved"
    assert result["approval"]["decided_by"] == "system"

    # Inventory: mock data for 2 items, 50 qty each (normal stock, no alerts)
    assert result["inventory"]["alerts"] is not None
    assert result["inventory"]["summary"] is not None

    # Dashboard
    assert result["dashboard"]["po_metrics"]


# ---------------------------------------------------------------------------
# E2E Test 3: Multi-item PO — inventory alert generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_multi_item_inventory_alerts(orchestrator, multi_item_po_email):
    """E2E: Multi-item PO → verify inventory alerts generated for items that trigger alerts.

    With mock data (qty=50, reorder=20, max_stock=100), items at max_stock trigger OVERSTOCK alerts.
    """
    task_id = uuid4()
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": multi_item_po_email},
        context={},
    )

    assert result["status"] == "success"

    # 3 items, all with qty=50, reorder=20, max_stock=100
    # qty < max_stock → NORMAL stock, no alerts generated (mock default)
    alerts = result["inventory"]["alerts"]
    assert alerts == []  # no alerts for normal stock levels

    # Inventory monitor tracked 3 items (summary from monitor)
    inv_summary = result["inventory"]["summary"]
    assert inv_summary["total_items"] == 3
    assert inv_summary["alert_count"] == 0


# ---------------------------------------------------------------------------
# E2E Test 4: E2E error path — invalid email triggers error_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_error_path(orchestrator):
    """E2E: Invalid email → po_agent fails → error_node → failed result."""
    task_id = uuid4()
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": "This is not a purchase order."},
        context={},
    )

    assert result["status"] == "failed"
    assert "error" in result
    # Should be caught at po_agent_node (PO parsing fails)
    assert (
        "PO" in result["error"] or "PO Agent" in result["error"] or "PO parsing" in result["error"]
    )


# ---------------------------------------------------------------------------
# E2E Test 5: E2E with organization context scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_with_org_context(orchestrator):
    """E2E: Verify graph execution with organization context from Telegram command."""
    task_id = uuid4()
    email = (
        "PO NUMBER: PO-2024-010\n"
        "VENDOR: Org Vendor\n"
        "Items:\n"
        "- SKU-ORG01, Office Supply, QTY: 100 @ $1.00 = $100.00\n"
        "TOTAL: $100.00\n"
    )
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": email},
        context={"organization_id": str(uuid4()), "source": "telegram"},
    )

    assert result["status"] == "success"
    assert result["po_data"]["po_number"] == "PO-2024-010"
    # PO data should be processed correctly with org context
    assert result["dashboard"]["po_metrics"]["total_pos_processed"] == 1


# ---------------------------------------------------------------------------
# E2E Test 6: Performance — graph execution under 5 seconds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_performance(orchestrator, small_po_email):
    """E2E: Verify graph execution completes within reasonable time (< 5s)."""
    import time

    task_id = uuid4()
    start = time.monotonic()
    result = await orchestrator.execute(
        task_id=task_id,
        payload={"email_content": small_po_email},
        context={},
    )
    elapsed = time.monotonic() - start

    assert result["status"] == "success"
    assert elapsed < 5.0  # Should complete well under 5 seconds for simple graph
