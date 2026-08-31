"""Supply Chain Agent Package

Registers supply chain domain agents and exposes factory helpers.
"""

from __future__ import annotations

from agents.supply_chain.inventory import InventoryMonitor, create_inventory_monitor
from agents.supply_chain.po_agent import PurchaseOrderAgent
from agents.supply_chain.reporting import SupplyChainReporter, create_supply_chain_reporter

__all__ = [
    "PurchaseOrderAgent",
    "SupplyChainReporter",
    "InventoryMonitor",
    "create_supply_chain_agents",
    "create_supply_chain_reporter",
    "create_inventory_monitor",
]


def create_supply_chain_agents(llm, settings) -> dict[str, PurchaseOrderAgent]:
    """Create all supply chain agents.  Returns agent dict keyed by
    qualified name so the orchestrator / registry can look them up."""
    return {
        "purchase_order_agent": PurchaseOrderAgent(
            llm=llm,
            settings=settings,
        ),
    }
