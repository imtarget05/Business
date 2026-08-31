"""Unit tests for Purchase Order Agent — PO parsing, classification, routing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.supply_chain.po_agent import (
    POItem,
    PurchaseOrder,
    PurchaseOrderAgent,
)
from packages.config.settings import Settings
from packages.contracts.enums import Domain
from packages.contracts.models import AgentDescriptor, TaskContext, TaskRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_po_agent(llm=None, with_llm=False):
    """Helper to create PO agent with optional mocked LLM and thresholds."""
    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 500.0, "manager_b": 5000.0}
    return PurchaseOrderAgent(llm=llm, settings=settings)


@pytest.fixture
def po_agent():
    return make_po_agent(llm=None)


@pytest.fixture
def po_agent_with_llm():
    llm = AsyncMock()
    return make_po_agent(llm=llm, with_llm=True)


@pytest.fixture
def descriptor():
    """Descriptor with supply_chain-prefixed capabilities matching Domain.SUPPLY_CHAIN."""
    return AgentDescriptor(
        name="purchase_order_agent",
        domain=Domain.SUPPLY_CHAIN,
        version="1",
        description="Test descriptor",
        capabilities=frozenset({"supply_chain.parse_po", "supply_chain.classify_po"}),
    )


# ---------------------------------------------------------------------------
# Descriptor tests
# ---------------------------------------------------------------------------


def test_po_agent_descriptor_created(po_agent):
    agent = po_agent
    assert agent.descriptor.name == "purchase_order_agent"
    assert agent.descriptor.domain == Domain.SUPPLY_CHAIN
    assert agent.descriptor.version == "1"
    caps = agent.descriptor.capabilities
    assert len(caps) > 0
    assert "supply_chain.parse_po" in caps
    assert "supply_chain.classify_po" in caps
    assert "supply_chain.route_po" in caps


def test_po_agent_descriptor_explicit(po_agent_with_llm):
    agent = PurchaseOrderAgent(llm=AsyncMock(), settings=Settings())
    assert agent.descriptor is not None
    assert agent.descriptor.name == "purchase_order_agent"


def test_po_agent_descriptor_supply_chain_caps(po_agent):
    caps = po_agent.descriptor.capabilities
    assert any(c.startswith("supply_chain.") for c in caps)


# ---------------------------------------------------------------------------
# Action routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_unsupported_action(po_agent):
    from uuid import uuid4

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="unsupported_action",
        payload={},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)
    assert resp.status.value == "rejected"
    assert resp.error is not None
    assert "unsupported action" in resp.error.message


@pytest.mark.asyncio
async def test_handle_missing_content(po_agent):
    from uuid import uuid4

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)
    assert resp.status.value == "rejected"
    assert "cannot parse PO" in resp.error.message


# ---------------------------------------------------------------------------
# PO parsing tests (rule-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_po_basic(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-001
VENDOR: Acme Corp
Vendor Email: vendor@example.com
Date: 2024-09-15

Items:
- SKU-001, Widget A - 10 units @ $5.00 each = $50.00 total
- SKU-002, Widget B - 5 units @ $10.00 each = $50.00 total

TOTAL: $100.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    po = resp.result["po"]
    assert po["po_number"] == "PO-2024-001", f"Expected PO-2024-001, got {po['po_number']!r}"
    assert po["vendor"] == "Acme Corp"
    assert po["vendor_email"] == "vendor@example.com"
    assert len(po["items"]) == 2, f"Expected 2 items, got {len(po['items'])}: {po['items']}"
    assert po["total"] == 100.0


@pytest.mark.asyncio
async def test_parse_po_minimal(po_agent):
    from uuid import uuid4

    text = "PO-2024-500\nVendor: Minimal Supply\nTotal: $250"

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_number"] == "PO-2024-500"
    assert resp.result["po"]["vendor"] == "Minimal Supply"
    assert resp.result["po"]["total"] == 250.0


@pytest.mark.asyncio
async def test_parse_po_no_po_data(po_agent):
    from uuid import uuid4

    text = "This is just a regular email, no purchase order here."

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)
    assert resp.status.value == "failed"
    assert resp.error is not None


# ---------------------------------------------------------------------------
# Classification tests (rule-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_new_order(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-010
VENDOR: New Supplier Inc
Items:
- SKU-100, Brand New Product, QTY: 100 @ $20.00 = $2000.00
TOTAL: $2000.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="classify_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_type"] == "new"


@pytest.mark.asyncio
async def test_classify_reorder(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-020
VENDOR: Existing Supplier
Items:
- SKU-200, Widget Restock Order, QTY: 50 @ $15.00 = $750.00
TOTAL: $750.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="classify_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_type"] == "reorder"


@pytest.mark.asyncio
async def test_classify_exchange(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-030
VENDOR: Return Center
Items:
- SKU-300, Defective Widget Exchange, QTY: 10 @ $100.00 = $1000.00
TOTAL: $1000.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="classify_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_type"] == "exchange"


# ---------------------------------------------------------------------------
# Routing / Policy tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_auto_approved_small_po(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-100
VENDOR: Small Vendor
Items:
- SKU-10, Small Item, QTY: 5 @ $10.00 = $50.00
TOTAL: $50.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "auto_approved"


@pytest.mark.asyncio
async def test_route_approval_required_manager_a(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-200
VENDOR: Medium Vendor
Items:
- SKU-20, Medium Item, QTY: 20 @ $30.00 = $600.00
TOTAL: $600.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "approval_required_manager_a"


@pytest.mark.asyncio
async def test_route_approval_required_manager_b(po_agent):
    from uuid import uuid4

    text = """\
PO NUMBER: PO-2024-300
VENDOR: Big Vendor
Items:
- SKU-30, Big Item, QTY: 10 @ $600.00 = $6000.00
TOTAL: $6000.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "approval_required_manager_b"


# ---------------------------------------------------------------------------
# LLM fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_parse_fallback_to_rule_based(po_agent_with_llm):
    """When LLM throws an error, agent falls back to rule-based parsing."""
    from uuid import uuid4

    from pydantic import BaseModel, Field

    class POExtractionSchema(BaseModel):
        po_number: str = Field(default="PO-2024-FALLBACK")
        vendor: str = Field(default="Fallback Test Corp")
        vendor_email: str | None = Field(default=None)
        date: str | None = Field(default=None)
        items: list[dict] = Field(default_factory=list)
        total: float = Field(default=300.0)

    po_agent_with_llm._llm.generate_structured = AsyncMock(side_effect=Exception("LLM parse error"))

    text = """\
PO NUMBER: PO-2024-FALLBACK
VENDOR: Fallback Test Corp
Total: $300.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent_with_llm.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_number"] == "PO-2024-FALLBACK"
    assert resp.result["po"]["vendor"] == "Fallback Test Corp"


@pytest.mark.asyncio
async def test_llm_classify_fallback_to_rule_based(po_agent_with_llm):
    """When LLM classification throws, agent falls back to rule-based classify."""
    from uuid import uuid4

    from pydantic import BaseModel, Field

    class POExtractionSchema(BaseModel):
        po_number: str = Field(default="PO-2024-CLASS-FALLBACK")
        vendor: str = Field(default="Test Vendor")
        vendor_email: str | None = Field(default=None)
        date: str | None = Field(default=None)
        items: list[dict] = Field(
            default=[
                {
                    "sku": "SKU-999",
                    "description": "Exchange Request Item",
                    "quantity": 1,
                    "unit_price": 10.0,
                    "total_price": 10.0,
                }
            ]
        )
        total: float = Field(default=10.0)

    # Mock structured parse to succeed (return dict, not a Pydantic model)
    po_agent_with_llm._llm.generate_structured = AsyncMock(
        return_value={
            "po_number": "PO-2024-CLASS-FALLBACK",
            "vendor": "Test Vendor",
            "vendor_email": None,
            "date": None,
            "items": [
                {
                    "sku": "SKU-999",
                    "description": "Exchange Request Item",
                    "quantity": 1,
                    "unit_price": 10.0,
                    "total_price": 10.0,
                }
            ],
            "total": 10.0,
        }
    )
    # Mock generate (for classification) to raise
    po_agent_with_llm._llm.generate = AsyncMock(side_effect=Exception("LLM classification error"))

    text = """\
PO NUMBER: PO-2024-CLASS-FALLBACK
VENDOR: Test Vendor
Items:
    - SKU-999, Exchange Request Item, QTY: 1 @ $10.00 = $10.00
TOTAL: $10.00
"""

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="classify_po",
        payload={"email_content": text},
        context=TaskContext(),
    )
    resp = await po_agent_with_llm.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_type"] == "exchange"


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------


def test_po_item_creation():
    item = POItem(
        sku="SKU-001",
        description="Widget",
        quantity=10,
        unit_price=5.0,
        total_price=50.0,
    )
    assert item.sku == "SKU-001"
    assert item.quantity == 10
    assert item.total_price == 50.0


def test_purchase_order_defaults():
    po = PurchaseOrder(po_number="PO-001", vendor="Test Vendor")
    assert po.po_type == "unknown"
    assert po.route == "auto_approved"
    assert po.items == []
    assert po.total == 0.0
