# -*- coding: utf-8 -*-
"""LangGraph-based supply chain workflow orchestration.

Wraps existing supply chain agents (PO Agent, Approval, Inventory, Reporting)
into a single StateGraph with checkpointing and conditional edges.

Graph flow:
    START → po_agent_node → [approval_node | inventory_node] → reporting_node → END
                                                                  └→ error_node → END (on failure)

Scope: Phase 1-2 supply chain agentic pipeline (per langgraph_agentic_plan.md).
Deferred: Langfuse tracing, human-in-the-loop approval UI, real inventory data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

from agents.supply_chain.inbound import process_inbound_email
from agents.supply_chain.po_agent import PurchaseOrderAgent
from agents.supply_chain.approval import ApprovalWorkflow, ApprovalState, needs_approval
from agents.supply_chain.inventory import InventoryMonitor, InventoryItem
from agents.supply_chain.reporting import SupplyChainReporter
from agents.supply_chain.po_guardrails import POAgentGuardrails
from agents.supply_chain.reporting_guardrails import ReportingGuardrails
from agents.supply_chain.inventory_guardrails import InventoryGuardrails
from agents.supply_chain.approval_guardrails import ApprovalGuardrails
from packages.config.settings import Settings, get_settings
from packages.contracts.models import TaskContext, TaskRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph State
# ---------------------------------------------------------------------------

@dataclass
class SupplyChainGraphState:
    """Carried by LangGraph between supply chain nodes.

    Mirrors the classic orchestrator state but is specific to supply chain
    workflow: PO data flows through parse → approval → inventory → report.
    """

    # --- Request ---
    task_id: UUID
    domain: str = "supply_chain"
    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    # --- PO data (output of PO Agent node) ---
    po_data: dict[str, Any] | None = None

    # --- Approval ---
    approval_state: str = "pending"
    approval_decision: str | None = None
    approval_decided_by: str | None = None

    # --- Inventory ---
    inventory_alerts: list[dict[str, Any]] = field(default_factory=list)
    inventory_summary: dict[str, Any] = field(default_factory=dict)

    # --- Reporting ---
    dashboard: dict[str, Any] | None = None
    report: dict[str, Any] | None = None

    # --- n8n export (Phase D) ---
    n8n_result: dict[str, Any] | None = None

    # --- Flow control ---
    current_step: str = "start"
    error: str | None = None
    step_history: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    final_result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helper: record step in history
# ---------------------------------------------------------------------------

def _record_step(state: SupplyChainGraphState, step: str, status: str) -> None:
    state.step_history.append({
        "step": step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
    })


# ---------------------------------------------------------------------------
# Node: PO Agent
# ---------------------------------------------------------------------------

async def po_agent_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Parse inbound email content using PurchaseOrderAgent.

    Input: state.payload["email_content"] (string)
    Output: state.po_data populated on success, or state.error on failure.
    """
    _record_step(state, "po_agent", "started")

    email_content = state.payload.get("email_content", "")
    if not isinstance(email_content, str) or not email_content.strip():
        state.error = "missing or invalid email_content"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "po_agent", "failed")
        return state

    # Get settings and create PO Agent with guardrails
    s = get_settings()
    po_agent = PurchaseOrderAgent(llm=None, settings=s)  #llm=None uses rule-based fallback
    guardrails = POAgentGuardrails(po_agent)

    # Build TaskRequest for guardrails
    from packages.contracts.models import TaskRequest, TaskContext
    req = TaskRequest(
        task_id=state.task_id,
        domain="supply_chain",
        action="parse_po",
        payload={"email_content": email_content},
        context=TaskContext(**state.context),
    )

    try:
        # Validate input via guardrails
        guardrails.validate_input(req)
        guardrails.check_permission(req)

        # Process email
        resp = await process_inbound_email(email_content, po_agent=po_agent)

        if resp.status.value == "success":
            state.po_data = resp.result["po"]
            state.current_step = "po_processed"
            _record_step(state, "po_agent", "success")
            # Nếu auto-approved, pre-populate approval fields để reporting_node có dữ liệu
            route = state.po_data.get("route", "") if state.po_data else ""
            if route == "auto_approved":
                state.approval_state = "approved"
                state.approval_decision = "auto_approved"
                state.approval_decided_by = "system"
        else:
            state.error = f"PO parsing failed: {resp.error}"
            state.current_step = "end"
            state.terminal = True
            state.final_result = {"status": "failed", "error": state.error}
            _record_step(state, "po_agent", "failed")

    except Exception as e:
        state.error = f"PO agent error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "po_agent", "failed")

    return state


# ---------------------------------------------------------------------------
# Node: Approval
# ---------------------------------------------------------------------------

async def approval_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Handle PO approval workflow.

    Only reached when po_data["route"] requires approval.
    For now (no human-in-the-loop UI): stub auto-approval.
    """
    _record_step(state, "approval", "started")

    # Guardrails: validate input before processing
    try:
        guardrails = ApprovalGuardrails()
        req = TaskRequest(
            task_id=state.task_id,
            domain="supply_chain",
            action="supply_chain_approve_po",
            payload={"po": state.po_data },
            context=TaskContext(**state.context),
        )
        guardrails.validate_input(req)
        guardrails.check_permission(req)
    except (ValueError, PermissionError) as e:
        state.error = f"Approval guardrails violation: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "approval", "guardrails_failed")
        return state

    if not state.po_data:
        state.error = "approval_node called without po_data"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "approval", "failed")
        return state

    # Check if approval needed
    if not needs_approval(state.po_data):
        # Auto-approved — skip to inventory
        state.approval_state = "approved"
        state.approval_decision = "auto_approved"
        state.approval_decided_by = "system"
        state.current_step = "approval_resolved"
        _record_step(state, "approval", "skipped_auto_approved")
        return state

    # Needs human approval — stub: auto-approve for now
    try:
        workflow = ApprovalWorkflow(po_data=state.po_data, approver_email="manager@company.com")
        # Transition to pending_human_approval state (use enum, not string)
        workflow._context.state = ApprovalState.PENDING_HUMAN_APPROVAL
        resolve_resp = await workflow.resolve(decision="approved", decided_by="system")

        if resolve_resp.status.value == "success":
            state.approval_state = "approved"
            state.approval_decision = "approved"
            state.approval_decided_by = "system"
            state.current_step = "approval_resolved"
            _record_step(state, "approval", "approved")
        else:
            state.approval_state = "rejected"
            state.approval_decision = "rejected"
            state.approval_decided_by = "system"
            state.current_step = "approval_resolved"
            _record_step(state, "approval", "rejected")

    except Exception as e:
        state.error = f"Approval error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "approval", "failed")

    return state


# ---------------------------------------------------------------------------
# Node: Inventory Monitor
# ---------------------------------------------------------------------------

async def inventory_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Check inventory levels for PO items, generate alerts, compute summary.

    Uses mock inventory data (real integration deferred).
    """
    _record_step(state, "inventory", "started")

    # Guardrails: validate input before processing
    try:
        guardrails = InventoryGuardrails()
        req = TaskRequest(
            task_id=state.task_id,
            domain="supply_chain",
            action="supply_chain_check_inventory",
            payload={"items": state.po_data.get("items", [])},
            context=TaskContext(**state.context),
        )
        guardrails.validate_input(req)
        guardrails.check_permission(req)
    except (ValueError, PermissionError) as e:
        state.error = f"Inventory guardrails violation: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "inventory", "guardrails_failed")
        return state

    if not state.po_data:
        state.error = "inventory_node called without po_data"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "inventory", "failed")
        return state

    try:
        monitor = InventoryMonitor()

        # Add PO items to inventory monitor with mock stock levels
        # In production, this would query real inventory system
        for item in state.po_data.get("items", []):
            sku = item.get("sku", "UNKNOWN")
            # Mock: deterministic stock levels for testing
            # Real implementation would fetch from inventory DB/ERP
            qty = 50  # default mock quantity
            reorder = 20  # default reorder point
            max_stock = 100  # default max stock
            unit_cost = item.get("unit_price", 0.0)
            monitor.add_item(
                InventoryItem(
                    sku=sku,
                    description=item.get("description", "Item"),
                    quantity_on_hand=qty,
                    reorder_point=reorder,
                    max_stock_level=max_stock,
                    unit_cost=unit_cost,
                )
            )

        # Get alerts and summary
        state.inventory_alerts = monitor.get_alerts()
        state.inventory_summary = monitor.get_summary()
        state.current_step = "inventory_checked"
        _record_step(state, "inventory", "success")

    except Exception as e:
        state.error = f"Inventory check error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "inventory", "failed")

    return state


# ---------------------------------------------------------------------------
# Node: Reporting
# ---------------------------------------------------------------------------

async def reporting_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Consolidate PO, approval, inventory data into dashboard/report.

    Always reached (success or failure path leads here via conditional edges).
    """
    _record_step(state, "reporting", "started")

    # Guardrails: validate input before processing
    try:
        guardrails = ReportingGuardrails()
        req = TaskRequest(
            task_id=state.task_id,
            domain="supply_chain",
            action="supply_chain_generate_report",
            payload={
                "report_type": "full_dashboard",
                "organization_id": state.context.get("organization_id", ""),
            },
            context=TaskContext(**state.context),
        )
        guardrails.validate_input(req)
        guardrails.check_permission(req)
    except (ValueError, PermissionError) as e:
        state.error = f"Reporting guardrails violation: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "reporting", "guardrails_failed")
        return state

    try:
        reporter = SupplyChainReporter()

        # Add mock data from workflow
        if state.po_data:
            reporter.add_mock_po(
                state.po_data["po_number"],
                state.po_data["vendor"],
                state.po_data["total"],
                state.po_data["route"],
                state.po_data["po_type"],
            )

        if state.approval_state:
            reporter.add_mock_approval(
                state.po_data["po_number"] if state.po_data else "unknown",
                state.approval_state,
                state.approval_decided_by or "system",
            )

        # Add inventory items to reporter
        for alert in state.inventory_alerts:
            reporter.add_mock_inventory_item(
                alert.get("sku", "UNKNOWN"),
                alert.get("description", "Item"),
                alert.get("current_quantity", 0),
                alert.get("threshold", 0),
                alert.get("max_stock_level", 100),
                alert.get("unit_cost", 0.0),
                alert.get("alert_type", "normal"),
            )

        # Generate dashboard
        state.dashboard = reporter.generate_full_dashboard()
        state.current_step = "report_generated"

        # Set final result
        state.final_result = {
            "status": "success",
            "dashboard": state.dashboard,
            "po_data": state.po_data,
            "approval": {
                "state": state.approval_state,
                "decision": state.approval_decision,
                "decided_by": state.approval_decided_by,
            },
            "inventory": {
                "alerts": state.inventory_alerts,
                "summary": state.inventory_summary,
            },
        }
        state.terminal = True
        _record_step(state, "reporting", "success")

    except Exception as e:
        state.error = f"Reporting error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "reporting", "failed")

    return state


# ---------------------------------------------------------------------------
# Node: n8n export (Phase D — Task 3.5)
# ---------------------------------------------------------------------------

async def n8n_export_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Export the approved PO to an n8n workflow via webhook (non-blocking).

    Runs after reporting. Exports only when the PO was approved (or
    auto-approved); rejected POs are skipped. Failures are captured on the
    state but do NOT fail the overall workflow — the PO pipeline already
    completed by this point.
    """
    try:
        from agents.supply_chain.n8n_client import N8nClient

        decision = state.approval_decision
        if decision not in ("approved", "auto_approved"):
            logger.info("n8n export skipped (PO not approved)")
            state.n8n_result = {
                "exported": False,
                "skipped": True,
                "reason": f"decision={decision}",
            }
            _record_step(state, "n8n_export", "skipped")
            return state

        client = N8nClient()
        export_payload = {
            "task_id": str(state.task_id),
            "po_data": state.po_data,
            "approval": {
                "state": state.approval_state,
                "decision": state.approval_decision,
                "decided_by": state.approval_decided_by,
            },
            "inventory": {
                "alerts": state.inventory_alerts,
                "summary": state.inventory_summary,
            },
            "dashboard": state.dashboard,
        }
        result = await client.export_po(export_payload)
        state.n8n_result = {
            "exported": result.exported,
            "webhook_url": result.webhook_url,
            "status_code": result.status_code,
            "error": result.error,
        }
        _record_step(
            state, "n8n_export", "success" if result.exported else "no_op"
        )
    except Exception as e:
        logger.warning(f"n8n export node error (non-fatal): {e}")
        state.n8n_result = {"exported": False, "error": str(e)}
        _record_step(state, "n8n_export", "error")
    return state


# ---------------------------------------------------------------------------
# Node: Error (terminal)
# ---------------------------------------------------------------------------

async def error_node(state: SupplyChainGraphState) -> SupplyChainGraphState:
    """Terminal node for failed workflows."""
    _record_step(state, "error", "terminal")
    state.terminal = True
    if not state.final_result:
        state.final_result = {
            "status": "failed",
            "error": state.error or "unknown error",
            "step": state.current_step,
        }
    return state


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------

def after_po_agent(state: SupplyChainGraphState) -> str:
    """Route after PO Agent: approval needed? → approval, else → inventory."""
    if state.error:
        return "error"
    if state.po_data and state.po_data.get("route") in (
        "approval_required_manager_a",
        "approval_required_manager_b",
    ):
        return "approval"
    return "inventory"


def after_approval(state: SupplyChainGraphState) -> str:
    """After approval: always → inventory (approved or rejected)."""
    if state.error:
        return "error"
    return "inventory"


def after_inventory(state: SupplyChainGraphState) -> str:
    """After inventory: always → reporting."""
    if state.error:
        return "error"
    return "reporting"


def after_reporting(state: SupplyChainGraphState) -> str:
    """Reporting is terminal → proceed to n8n export (non-blocking), then END."""
    return "n8n_export"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _build_checkpointer(settings: Settings) -> InMemorySaver:
    """Build an InMemorySaver checkpointer.

    Uses InMemorySaver for simplicity. For production with SQLite persistence,
    swap to a different checkpointer implementation when available.
    """
    return InMemorySaver()


def _build_supply_chain_graph(settings: Settings | None = None) -> Any:
    """Build and compile the supply chain LangGraph workflow.

    Returns compiled graph ready for ainvoke().
    """
    s = settings or get_settings()

    graph = StateGraph(SupplyChainGraphState)

    # Add nodes
    graph.add_node("po_agent", po_agent_node)
    graph.add_node("approval", approval_node)
    graph.add_node("inventory", inventory_node)
    graph.add_node("reporting", reporting_node)
    graph.add_node("n8n_export", n8n_export_node)
    graph.add_node("error", error_node)

    # Add edges
    graph.add_edge(START, "po_agent")
    graph.add_conditional_edges(
        "po_agent",
        after_po_agent,
        {
            "approval": "approval",
            "inventory": "inventory",
            "error": "error",
        },
    )
    graph.add_conditional_edges(
        "approval",
        after_approval,
        {
            "inventory": "inventory",
            "error": "error",
        },
    )
    graph.add_conditional_edges(
        "inventory",
        after_inventory,
        {
            "reporting": "reporting",
            "error": "error",
        },
    )
    graph.add_conditional_edges(
        "reporting",
        after_reporting,
        {
            "n8n_export": "n8n_export",
            "end": END,
        },
    )
    graph.add_edge("n8n_export", END)
    graph.add_edge("error", END)

    # Compile with checkpoint
    checkpointer = _build_checkpointer(s)
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Orchestrator wrapper
# ---------------------------------------------------------------------------

class SupplyChainGraphOrchestrator:
    """LangGraph-backed supply chain workflow orchestrator.

    Public API:
        execute(request) -> dict  — runs the full supply chain graph
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._graph = _build_supply_chain_graph(self._settings)

    async def execute(self, task_id: UUID, payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the supply chain graph for a single PO inbound request.

        Args:
            task_id: Unique task identifier (UUID).
            payload: Request payload, must contain "email_content" key.
            context: Optional context dict (organization_id, etc.).

        Returns:
            Final result dict with status, dashboard, po_data, etc.
        """
        initial_state = SupplyChainGraphState(
            task_id=task_id,
            domain="supply_chain",
            action="process_po",
            payload=payload,
            context=context or {},
        )

        start_time = datetime.now(timezone.utc)

        config = {
            "configurable": {"thread_id": str(task_id)},
            "search": {"value": "latest", "limit": 1},
        }

        result = await self._graph.ainvoke(initial_state, config)

        end_time = datetime.now(timezone.utc)
        duration_ms = (end_time - start_time).total_seconds() * 1000
        step_count = len(result.get("step_history", []))

        # Add evaluation metrics to final result
        if result.get("final_result"):
            result["final_result"]["_metrics"] = {
                "duration_ms": round(duration_ms, 2),
                "step_count": step_count,
                "timestamp": end_time.isoformat(),
            }

        # ainvoke returns the final state as a dict
        final = result.get("final_result")
        final_status = final.get("status") if isinstance(final, dict) else None
        # Build rich result envelope for observability + downstream consumers
        envelope = {
            "status": "failed" if final_status == "failed" else ("success" if final is not None else "failed"),
            "task_id": str(task_id),
            "po_data": result.get("po_data"),
            "approval": {
                "state": result.get("approval_state"),
                "decision": result.get("approval_decision"),
                "decided_by": result.get("approval_decided_by"),
            },
            "inventory": {
                "alerts": result.get("inventory_alerts"),
                "summary": result.get("inventory_summary"),
            },
            "dashboard": result.get("dashboard"),
            "report": result.get("report"),
            "n8n_result": result.get("n8n_result"),
            "step_history": result.get("step_history"),
            "final_result": final,
            "_metrics": {
                "duration_ms": round(duration_ms, 2),
                "step_count": step_count,
                "timestamp": end_time.isoformat(),
            },
        }
        if envelope["status"] == "failed":
            envelope["error"] = (final.get("error") if isinstance(final, dict) else None) or result.get("error") or "no result"
        return envelope


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_supply_chain_graph_orchestrator(settings: Settings | None = None) -> SupplyChainGraphOrchestrator:
    """Factory function for SupplyChainGraphOrchestrator."""
    return SupplyChainGraphOrchestrator(settings)
