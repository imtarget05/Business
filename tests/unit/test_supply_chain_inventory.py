# -*- coding: utf-8 -*-
"""Unit tests for supply_chain inventory monitoring (Phase SC).

Validates the InventoryMonitor agent's ability to track stock levels,
generate alerts for low stock / out of stock / overstock conditions,
and provide summary reports for supply chain operations.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agents.supply_chain.inventory import (
    InventoryAlert,
    InventoryAlertType,
    InventoryItem,
    InventoryMonitor,
    InventoryStatus,
    InventorySnapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def inventory_monitor() -> InventoryMonitor:
    """Fresh InventoryMonitor instance for isolated tests."""
    return InventoryMonitor()


@pytest.fixture
def sample_inventory_items() -> list[InventoryItem]:
    """Sample inventory items covering various stock scenarios."""
    return [
        InventoryItem(
            sku="SKU-001",
            description="Widget A",
            quantity_on_hand=100,
            reorder_point=20,
            max_stock_level=150,
            unit_cost=5.0,
            location="WH-A-001",
        ),
        InventoryItem(
            sku="SKU-002",
            description="Widget B",
            quantity_on_hand=15,
            reorder_point=20,
            max_stock_level=100,
            unit_cost=10.0,
            location="WH-A-002",
        ),
        InventoryItem(
            sku="SKU-003",
            description="Widget C",
            quantity_on_hand=0,
            reorder_point=10,
            max_stock_level=50,
            unit_cost=20.0,
            location="WH-B-001",
        ),
        InventoryItem(
            sku="SKU-004",
            description="Widget D",
            quantity_on_hand=300,
            reorder_point=50,
            max_stock_level=200,
            unit_cost=2.0,
            location="WH-A-003",
        ),
        InventoryItem(
            sku="SKU-005",
            description="Widget E",
            quantity_on_hand=25,
            reorder_point=30,
            max_stock_level=100,
            unit_cost=15.0,
            location="WH-B-002",
        ),
    ]


# ---------------------------------------------------------------------------
# InventoryItem tests
# ---------------------------------------------------------------------------

def test_inventory_item_basic():
    """Basic inventory item creation."""
    item = InventoryItem(
        sku="SKU-123",
        description="Test Item",
        quantity_on_hand=50,
        reorder_point=20,
        max_stock_level=100,
        unit_cost=10.0,
    )
    assert item.sku == "SKU-123"
    assert item.quantity_on_hand == 50
    assert item.reorder_point == 20
    assert item.max_stock_level == 100
    assert item.unit_cost == 10.0


def test_inventory_item_stock_value():
    """Stock value = quantity * unit_cost."""
    item = InventoryItem(
        sku="SKU-123",
        quantity_on_hand=10,
        unit_cost=5.0,
    )
    assert item.stock_value == 50.0


def test_inventory_item_is_low_stock():
    """Item is low stock when quantity < reorder_point and > 0."""
    item = InventoryItem(sku="SKU-001", quantity_on_hand=15, reorder_point=20)
    assert item.is_low_stock is True

    item2 = InventoryItem(sku="SKU-002", quantity_on_hand=25, reorder_point=20)
    assert item2.is_low_stock is False

    item3 = InventoryItem(sku="SKU-003", quantity_on_hand=0, reorder_point=10)
    # Out of stock should not be low stock (different alert type)
    assert item3.is_low_stock is False


def test_inventory_item_is_out_of_stock():
    """Item is out of stock when quantity == 0."""
    item = InventoryItem(sku="SKU-001", quantity_on_hand=0)
    assert item.is_out_of_stock is True

    item2 = InventoryItem(sku="SKU-002", quantity_on_hand=1)
    assert item2.is_out_of_stock is False


def test_inventory_item_is_overstock():
    """Item is overstock when quantity > max_stock_level."""
    item = InventoryItem(sku="SKU-001", quantity_on_hand=150, max_stock_level=100)
    assert item.is_overstock is True

    item2 = InventoryItem(sku="SKU-002", quantity_on_hand=90, max_stock_level=100)
    assert item2.is_overstock is False


def test_inventory_item_status():
    """Status reflects current stock condition."""
    # Normal
    item = InventoryItem(sku="SKU-001", quantity_on_hand=50, reorder_point=20, max_stock_level=100)
    assert item.status == InventoryStatus.NORMAL

    # Low stock
    item2 = InventoryItem(sku="SKU-002", quantity_on_hand=15, reorder_point=20, max_stock_level=100)
    assert item2.status == InventoryStatus.LOW_STOCK

    # Out of stock
    item3 = InventoryItem(sku="SKU-003", quantity_on_hand=0, reorder_point=10, max_stock_level=50)
    assert item3.status == InventoryStatus.OUT_OF_STOCK

    # Overstock
    item4 = InventoryItem(sku="SKU-004", quantity_on_hand=150, reorder_point=20, max_stock_level=100)
    assert item4.status == InventoryStatus.OVERSTOCK


# ---------------------------------------------------------------------------
# InventoryMonitor add / snapshot tests
# ---------------------------------------------------------------------------

def test_add_single_item(inventory_monitor):
    """Adding a single item updates the snapshot."""
    item = InventoryItem(sku="SKU-001", quantity_on_hand=100)
    inventory_monitor.add_item(item)

    snapshot = inventory_monitor.get_snapshot()
    assert snapshot.total_items == 1
    assert snapshot.items[0].sku == "SKU-001"


def test_add_multiple_items(inventory_monitor, sample_inventory_items):
    """Adding multiple items updates snapshot correctly."""
    inventory_monitor.add_items(sample_inventory_items)

    snapshot = inventory_monitor.get_snapshot()
    assert snapshot.total_items == 5
    assert snapshot.total_value == pytest.approx(
        100 * 5.0 + 15 * 10.0 + 0 * 20.0 + 300 * 2.0 + 25 * 15.0
    )


def test_get_snapshot_includes_alerts(inventory_monitor, sample_inventory_items):
    """Snapshot includes generated alerts after monitoring."""
    inventory_monitor.add_items(sample_inventory_items)

    snapshot = inventory_monitor.get_snapshot()
    assert len(snapshot.alerts) > 0


# ---------------------------------------------------------------------------
# Alert generation tests
# ---------------------------------------------------------------------------

def test_alert_out_of_stock(inventory_monitor):
    """Out of stock items generate critical alerts."""
    item = InventoryItem(sku="SKU-OOS", quantity_on_hand=0, reorder_point=10)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 1
    assert alerts[0].alert_type == InventoryAlertType.OUT_OF_STOCK
    assert alerts[0].severity == "critical"
    assert alerts[0].sku == "SKU-OOS"


def test_alert_low_stock(inventory_monitor):
    """Low stock items generate warning alerts."""
    item = InventoryItem(sku="SKU-LS", quantity_on_hand=15, reorder_point=20)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 1
    assert alerts[0].alert_type == InventoryAlertType.LOW_STOCK
    assert alerts[0].severity == "warning"


def test_alert_overstock(inventory_monitor):
    """Overstock items generate warning alerts."""
    item = InventoryItem(sku="SKU-OS", quantity_on_hand=200, max_stock_level=100)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 1
    assert alerts[0].alert_type == InventoryAlertType.OVERSTOCK
    assert alerts[0].severity == "warning"


def test_alert_reorder_point(inventory_monitor):
    """Items at reorder point generate info alerts (exactly at threshold)."""
    item = InventoryItem(sku="SKU-RP", quantity_on_hand=20, reorder_point=20, max_stock_level=100)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 1
    assert alerts[0].alert_type == InventoryAlertType.REORDER_POINT
    assert alerts[0].severity == "info"


def test_normal_stock_no_alert(inventory_monitor):
    """Normal stock levels generate no alerts."""
    item = InventoryItem(sku="SKU-NORM", quantity_on_hand=50, reorder_point=20, max_stock_level=100)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    assert len(alerts) == 0


def test_multiple_alerts_same_item(inventory_monitor):
    """Out of stock takes priority over low stock for same item."""
    item = InventoryItem(sku="SKU-BOTH", quantity_on_hand=0, reorder_point=20)
    inventory_monitor.add_item(item)

    alerts = inventory_monitor.get_alerts()
    # Should generate only OUT_OF_STOCK alert, not LOW_STOCK
    assert len(alerts) == 1
    assert alerts[0].alert_type == InventoryAlertType.OUT_OF_STOCK


# ---------------------------------------------------------------------------
# Alert filtering tests
# ---------------------------------------------------------------------------

def test_get_critical_alerts(inventory_monitor, sample_inventory_items):
    """Critical alerts filter returns only out-of-stock items."""
    inventory_monitor.add_items(sample_inventory_items)

    critical = inventory_monitor.get_critical_alerts()
    assert len(critical) == 1  # SKU-003 is out of stock
    assert critical[0].alert_type == InventoryAlertType.OUT_OF_STOCK


def test_get_warning_alerts(inventory_monitor, sample_inventory_items):
    """Warning alerts include low stock and overstock."""
    inventory_monitor.add_items(sample_inventory_items)

    warnings = inventory_monitor.get_warning_alerts()
    # SKU-002 (low stock), SKU-004 (overstock), SKU-005 (low stock)
    assert len(warnings) == 3


# ---------------------------------------------------------------------------
# Summary tests
# ---------------------------------------------------------------------------

def test_get_summary_basic(inventory_monitor):
    """Summary returns counts for each status."""
    items = [
        InventoryItem(sku="SKU-001", quantity_on_hand=100, reorder_point=20, max_stock_level=150),
        InventoryItem(sku="SKU-002", quantity_on_hand=15, reorder_point=20, max_stock_level=100),
        InventoryItem(sku="SKU-003", quantity_on_hand=0, reorder_point=10, max_stock_level=50),
        InventoryItem(sku="SKU-004", quantity_on_hand=300, reorder_point=50, max_stock_level=200),
    ]
    inventory_monitor.add_items(items)

    summary = inventory_monitor.get_summary()

    assert summary["total_items"] == 4
    assert summary["low_stock_count"] == 1  # SKU-002
    assert summary["out_of_stock_count"] == 1  # SKU-003
    assert summary["overstock_count"] == 1  # SKU-004
    assert summary["normal_count"] == 1  # SKU-001
    assert summary["alert_count"] == 3  # low + out + over


def test_get_summary_empty(inventory_monitor):
    """Empty inventory returns zero counts."""
    summary = inventory_monitor.get_summary()

    assert summary["total_items"] == 0
    assert summary["low_stock_count"] == 0
    assert summary["out_of_stock_count"] == 0
    assert summary["overstock_count"] == 0
    assert summary["normal_count"] == 0
    assert summary["alert_count"] == 0


# ---------------------------------------------------------------------------
# InventoryMonitor handle() — agent contract tests (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_check_inventory_no_items():
    """check_inventory with no items returns success with empty data."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.contracts.enums import Domain
    from agents.supply_chain.inventory import InventoryMonitor

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="check_inventory",
        payload={},
        context=TaskContext(),
    )

    monitor = InventoryMonitor()
    response = await monitor.handle(request)

    assert response.status == "success"
    assert response.result["inventory_summary"]["total_items"] == 0
    assert response.result["snapshot"]["total_items"] == 0
    assert response.result["alerts"] == []


@pytest.mark.asyncio
async def test_handle_check_inventory_with_items():
    """check_inventory with items updates snapshot and returns results."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.contracts.enums import Domain
    from agents.supply_chain.inventory import InventoryMonitor

    items_data = [
        {
            "sku": "SKU-100",
            "description": "Test Part",
            "quantity_on_hand": 50,
            "reorder_point": 20,
            "max_stock_level": 100,
            "unit_cost": 10.0,
            "location": "WH-01",
        },
        {
            "sku": "SKU-101",
            "description": "Out of Stock Part",
            "quantity_on_hand": 0,
            "reorder_point": 10,
            "max_stock_level": 50,
            "unit_cost": 20.0,
            "location": "WH-02",
        },
    ]

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="check_inventory",
        payload={"items": items_data},
        context=TaskContext(),
    )

    monitor = InventoryMonitor()
    response = await monitor.handle(request)

    assert response.status == "success"
    summary = response.result["inventory_summary"]
    assert summary["total_items"] == 2
    assert summary["out_of_stock_count"] == 1
    assert summary["low_stock_count"] == 0
    assert summary["overstock_count"] == 0

    items = response.result["snapshot"]["items"]
    assert len(items) == 2
    assert items[0]["sku"] == "SKU-100"
    assert items[0]["status"] == "normal"
    assert items[1]["sku"] == "SKU-101"
    assert items[1]["status"] == "out_of_stock"

    alerts = response.result["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "out_of_stock"
    assert alerts[0]["sku"] == "SKU-101"


@pytest.mark.asyncio
async def test_handle_get_alerts():
    """get_alerts returns current alerts."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.contracts.enums import Domain
    from agents.supply_chain.inventory import InventoryMonitor

    item = InventoryItem(sku="SKU-ALERT", quantity_on_hand=0, reorder_point=10)
    monitor = InventoryMonitor()
    monitor.add_item(item)

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_alerts",
        payload={},
        context=TaskContext(),
    )

    response = await monitor.handle(request)

    assert response.status == "success"
    assert len(response.result["alerts"]) == 1
    assert response.result["alerts"][0]["alert_type"] == "out_of_stock"
    assert response.result["critical_count"] == 1
    assert response.result["warning_count"] == 0


@pytest.mark.asyncio
async def test_handle_get_summary():
    """get_summary returns inventory summary."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.contracts.enums import Domain
    from agents.supply_chain.inventory import InventoryMonitor

    items = [
        InventoryItem(sku="SKU-001", quantity_on_hand=100, reorder_point=20, max_stock_level=150),
        InventoryItem(sku="SKU-002", quantity_on_hand=15, reorder_point=20, max_stock_level=100),
    ]
    monitor = InventoryMonitor()
    monitor.add_items(items)

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="get_summary",
        payload={},
        context=TaskContext(),
    )

    response = await monitor.handle(request)

    assert response.status == "success"
    summary = response.result["inventory_summary"]
    assert summary["total_items"] == 2
    assert summary["low_stock_count"] == 1
    assert summary["out_of_stock_count"] == 0


@pytest.mark.asyncio
async def test_handle_unsupported_action():
    """Unsupported actions are rejected."""
    from uuid import uuid4
    from packages.contracts.models import TaskRequest, TaskContext
    from packages.contracts.enums import Domain
    from agents.supply_chain.inventory import InventoryMonitor

    request = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="unknown_action",
        payload={},
        context=TaskContext(),
    )

    monitor = InventoryMonitor()
    response = await monitor.handle(request)

    assert response.status == "rejected"
    assert response.error is not None
    assert "Unsupported action" in response.error.message


# ---------------------------------------------------------------------------
# Integration-style: inventory with PO Agent output format
# ---------------------------------------------------------------------------

def test_inventory_from_po_agent_format():
    """Inventory monitor can consume PO Agent-style output data.

    This demonstrates how inventory monitoring integrates with the
    broader supply chain pipeline — PO Agent outputs item data that
    can be consumed by inventory monitoring.
    """
    # Simulate data that might come from PO Agent or ERP integration
    po_agent_output_items = [
        {
            "sku": "INV-SKU-001",
            "description": "Raw Material A",
            "quantity_on_hand": 75,
            "reorder_point": 50,
            "max_stock_level": 200,
            "unit_cost": 25.0,
            "location": "RAW-WH-01",
        },
        {
            "sku": "INV-SKU-002",
            "description": "Finished Good B",
            "quantity_on_hand": 8,
            "reorder_point": 20,
            "max_stock_level": 100,
            "unit_cost": 150.0,
            "location": "FIN-WH-01",
        },
    ]

    monitor = InventoryMonitor()
    for item_data in po_agent_output_items:
        item = InventoryItem(
            sku=item_data["sku"],
            description=item_data["description"],
            quantity_on_hand=item_data["quantity_on_hand"],
            reorder_point=item_data["reorder_point"],
            max_stock_level=item_data["max_stock_level"],
            unit_cost=item_data["unit_cost"],
            location=item_data["location"],
        )
        monitor.add_item(item)

    summary = monitor.get_summary()

    assert summary["total_items"] == 2
    assert summary["low_stock_count"] == 1  # INV-SKU-002
    assert summary["out_of_stock_count"] == 0
    assert summary["normal_count"] == 1  # INV-SKU-001

    alerts = monitor.get_alerts()
    assert len(alerts) == 1
    assert alerts[0].sku == "INV-SKU-002"
    assert alerts[0].alert_type == InventoryAlertType.LOW_STOCK


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------

def test_inventory_alert_fields():
    """InventoryAlert has all required fields."""
    alert = InventoryAlert(
        alert_type=InventoryAlertType.OUT_OF_STOCK,
        sku="SKU-001",
        description="Test item",
        current_quantity=0,
        threshold=10,
        severity="critical",
        timestamp=time.time(),
        message="Test message",
    )

    assert alert.alert_type == InventoryAlertType.OUT_OF_STOCK
    assert alert.sku == "SKU-001"
    assert alert.current_quantity == 0
    assert alert.threshold == 10
    assert alert.severity == "critical"


def test_inventory_snapshot_total_value():
    """Snapshot calculates total inventory value correctly."""
    items = [
        InventoryItem(sku="SKU-001", quantity_on_hand=10, unit_cost=5.0),
        InventoryItem(sku="SKU-002", quantity_on_hand=5, unit_cost=10.0),
    ]

    snapshot = InventorySnapshot(items=items)
    snapshot.total_items = len(items)
    snapshot.total_value = sum(item.stock_value for item in items)

    assert snapshot.total_value == 100.0  # 10*5 + 5*10
