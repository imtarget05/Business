# -*- coding: utf-8 -*-
"""E2E: Supply-chain LangGraph flow reaches an approved terminal state (offline).

Drives the full graph: inbound PO email -> PO agent -> (auto-approve low amount /
manual-approve high amount) -> inventory -> reporting, asserting the workflow
completes with an approved decision. Uses the in-memory checkpointer so the suite
stays green with no Postgres.
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from agents.supply_chain.graph import SupplyChainGraphOrchestrator
from packages.config.settings import Settings


@pytest.fixture
def sc_orchestrator():
    # InMemorySaver is the default when langgraph_checkpoint_url is unset.
    return SupplyChainGraphOrchestrator(settings=Settings())


SMALL_PO_EMAIL = (
    "PO NUMBER: PO-2024-001\n"
    "VENDOR: Test Vendor\n"
    "Items:\n"
    "- SKU-001, Widget, QTY: 10 @ $5.00 = $50.00\n"
    "TOTAL: $50.00\n"
)

LARGE_PO_EMAIL = (
    "PO NUMBER: PO-2024-002\n"
    "VENDOR: Big Vendor\n"
    "Items:\n"
    "- SKU-002, Heavy Machine, QTY: 2 @ $3000.00 = $6000.00\n"
    "TOTAL: $6000.00\n"
)


@pytest.mark.e2e
async def test_supply_chain_low_amount_auto_approved(sc_orchestrator):
    result = await sc_orchestrator.execute(
        task_id=uuid4(),
        payload={"email_content": SMALL_PO_EMAIL},
        context={"organization_id": str(uuid4())},
    )
    assert result["status"] == "success"
    assert result["po_data"]["po_number"] == "PO-2024-001"
    assert result["po_data"]["route"] == "auto_approved"
    assert result["approval"]["state"] == "approved"
    assert result["approval"]["decision"] == "auto_approved"
    assert result["dashboard"]


@pytest.mark.e2e
async def test_supply_chain_high_amount_manual_approved(sc_orchestrator):
    result = await sc_orchestrator.execute(
        task_id=uuid4(),
        payload={"email_content": LARGE_PO_EMAIL},
        context={"organization_id": str(uuid4())},
    )
    assert result["status"] == "success"
    assert result["po_data"]["po_number"] == "PO-2024-002"
    assert result["po_data"]["route"] == "approval_required_manager_b"
    # Graph stub auto-approves the manager-B routed PO.
    assert result["approval"]["state"] == "approved"
    assert result["approval"]["decision"] == "approved"
    assert result["inventory"]["alerts"] is not None
    assert result["dashboard"]["po_metrics"]


@pytest.mark.e2e
async def test_supply_chain_reaches_terminal_state(sc_orchestrator):
    result = await sc_orchestrator.execute(
        task_id=uuid4(),
        payload={"email_content": SMALL_PO_EMAIL},
        context={},
    )
    # Terminal state is encoded in final_result + dashboard presence.
    assert result["status"] == "success"
    assert result["final_result"]["status"] == "success"
    assert result["final_result"]["approval"]["state"] == "approved"
