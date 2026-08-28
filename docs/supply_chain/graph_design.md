# Supply Chain LangGraph Workflow — Graph Design

> Document version: 1.0  
> Created: 2026-08-28  
> Status: **Approved** — ready for implementation

---

## 1. Overview

LangGraph-based orchestration workflow for the supply chain agent pipeline:

```
Inbound PO Email → PO Agent (parse/classify/route) → [Approval if needed] → Inventory Check → Reporting
```

This graph wraps the existing supply chain agents (`PurchaseOrderAgent`, `ApprovalWorkflow`, `InventoryMonitor`, `SupplyChainReporter`) into a single LangGraph `StateGraph` with explicit node transitions, checkpointing, and conditional edges.

**Scope (Phase 1-2 implementation):**
- PO Agent node (parse + classify + route)
- Conditional edge: if `route` requires approval → Approval node; else → Inventory node
- Inventory Monitor node (alerts + summary)
- Reporting Agent node (consolidate + dashboard)
- Always ends at Reporting node (success or failure)

**Out of scope for now (deferred):**
- Langfuse tracing (Phase 2.4 in plan — deferred)
- Human-in-the-loop approval UI integration (approval node is simulated/stubbed)
- Real Gmail inbound trigger (n8n workflow handles this externally)

---

## 2. Graph State Schema

```python
# agents/supply_chain/graph.py — SupplyChainGraphState

from dataclasses import dataclass, field
from uuid import UUID
from typing import Any

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
    po_data: dict[str, Any] | None = None  # structured PO: po_number, vendor, items, total, route, po_type

    # --- Approval ---
    approval_state: str = "pending"  # pending, pending_approval, approved, rejected, expired
    approval_decision: str | None = None
    approval_decided_by: str | None = None

    # --- Inventory ---
    inventory_alerts: list[dict[str, Any]] = field(default_factory=list)
    inventory_summary: dict[str, Any] = field(default_factory=dict)

    # --- Reporting ---
    dashboard: dict[str, Any] | None = None
    report: dict[str, Any] | None = None

    # --- Flow control ---
    current_step: str = "start"  # start, po_processed, approval_pending, approval_resolved, inventory_checked, report_generated, end
    error: str | None = None
    step_history: list[dict[str, Any]] = field(default_factory=list)  # [{step, timestamp, status}]

    # --- Terminal flag ---
    terminal: bool = False
    final_result: dict[str, Any] | None = None
```

---

## 3. Nodes

### 3.1 `po_agent_node` — Purchase Order Processing

**Signature:** `async def po_agent_node(state: SupplyChainGraphState) -> SupplyChainGraphState`

**Responsibility:** Parse inbound email content using `PurchaseOrderAgent`, classify PO type, determine route based on policy thresholds.

**Input:** `state.payload["email_content"]` (string) or `state.payload` containing email data.

**Process:**
1. Extract `email_content` from `state.payload`.
2. Create/Re-use `PurchaseOrderAgent` (from registry or constructed with mock LLM for testing).
3. Call `await process_inbound_email(email_content, po_agent=po_agent)` — existing function in `agents/supply_chain/inbound.py`.
4. On success: store `state.po_data = resp.result["po"]`, set `state.current_step = "po_processed"`.
5. On failure: set `state.error`, `state.current_step = "end"`, `state.terminal = True`, `state.final_result = {"status": "failed", "error": state.error}`.
6. Record step in `state.step_history`: `{"step": "po_agent", "timestamp": now(), "status": "success"|"failed"}`.

**Output:** Updated `SupplyChainGraphState` with `po_data` populated (or error).

---

### 3.2 `approval_node` — Approval Workflow (conditional)

**Signature:** `async def approval_node(state: SupplyChainGraphState) -> SupplyChainGraphState`

**Responsibility:** Handle PO approval workflow. Only reached when `po_data["route"]` requires approval (i.e., `approval_required_manager_a` or `approval_required_manager_b`).

**Input:** `state.po_data` (must be non-None).

**Process:**
1. Check `needs_approval(state.po_data)` — existing function in `agents/supply_chain/approval.py`.
2. If `needs_approval` returns `False` (auto-approved): skip to inventory node. Set `state.approval_state = "approved"`, `state.current_step = "inventory_check"`.
3. If approval needed:
   - Create `ApprovalWorkflow(po_data=state.po_data, approver_email=...)`.
   - Set `state.approval_state = "pending_approval"`.
   - **For now (no human-in-the-loop UI):** Simulate approval by calling `await workflow.resolve(decision="approved", decided_by="system")` — stubbed auto-approval. In production, this would wait for human decision via API/webhook.
   - On resolution: store `state.approval_state`, `state.approval_decision`, `state.approval_decided_by`.
   - Set `state.current_step = "approval_resolved"`.
4. Record step in `state.step_history`.

**Output:** Updated state with approval resolved (approved or rejected).

**Conditional edge from PO node:** If `po_data.route` in `{"approval_required_manager_a", "approval_required_manager_b"}` → go to `approval_node`. Else → skip to `inventory_node`.

---

### 3.3 `inventory_node` — Inventory Monitoring

**Signature:** `async def inventory_node(state: SupplyChainGraphState) -> SupplyChainGraphState`

**Responsibility:** Check inventory levels for items in the PO, generate alerts (low stock, out-of-stock, overstock), compute summary.

**Input:** `state.po_data` (for item SKUs), existing `InventoryMonitor` instance.

**Process:**
1. Create `InventoryMonitor()` (fresh instance or from registry).
2. For each item in `state.po_data["items"]`:
   - Add item to inventory monitor with mock/hardcoded stock levels (for testing; real implementation would query inventory system).
   - **Note:** Real inventory data integration is out of scope — using mock data per plan.
3. Call `inventory_monitor.get_alerts()` → store in `state.inventory_alerts`.
4. Call `inventory_monitor.get_summary()` → store in `state.inventory_summary`.
5. Set `state.current_step = "inventory_checked"`.
6. Record step in `state.step_history`.

**Output:** Updated state with `inventory_alerts` and `inventory_summary`.

---

### 3.4 `reporting_node` — Reporting / Dashboard

**Signature:** `async def reporting_node(state: SupplyChainGraphState) -> SupplyChainGraphState`

**Responsibility:** Consolidate PO data, approval decision, inventory alerts into a final dashboard/report.

**Input:** `state.po_data`, `state.approval_state`, `state.inventory_alerts`, `state.inventory_summary`.

**Process:**
1. Create `SupplyChainReporter()` (fresh instance or from registry).
2. Add mock data to reporter:
   - `reporting_agent.add_mock_po(po_data["po_number"], po_data["vendor"], po_data["total"], po_data["route"], po_data["po_type"])`
   - `reporting_agent.add_mock_approval(po_data["po_number"], approval_state, approval_decided_by or "system")`
   - For each inventory item: `reporting_agent.add_mock_inventory_item(...)`
3. Call `reporting_agent.generate_full_dashboard()` → store in `state.dashboard`.
4. Optionally generate specific reports: `get_po_report`, `get_inventory_report`, `get_approval_report`.
5. Set `state.current_step = "report_generated"`.
6. Set `state.final_result = {"status": "success", "dashboard": state.dashboard, "po_data": state.po_data, "approval": {...}, "inventory": {...}}`.
7. Set `state.terminal = True`.
8. Record step in `state.step_history`.

**Output:** Updated state with `dashboard` and `final_result`. Terminal node — graph ends here.

---

### 3.5 `error_node` — Error Handling (terminal)

**Signature:** `async def error_node(state: SupplyChainGraphState) -> SupplyChainGraphState`

**Responsibility:** Terminal node for failed workflows. Records error, sets final result.

**Process:**
1. Set `state.terminal = True`.
2. Set `state.final_result = {"status": "failed", "error": state.error, "step": state.current_step}`.
3. Record step in `state.step_history`.
4. Return state → flows to END.

---

## 4. Graph Topology (Edges)

```
          ┌─────────────┐
          │   START()   │
          └──────┬──────┘
                 │
                 ▼
        ┌────────────────┐
        │  po_agent_node │
        └───────┬────────┘
                │
        ┌───────┴────────┐
        │  route check   │
        └───────┬────────┘
          ┌─────┴─────┐
          │           │
          ▼           ▼
┌────────────────┐  ┌────────────────┐
│  approval_node │  │ inventory_node │
│ (if needs      │  │ (if auto-      │
│  approval)     │  │  approved or   │
└───────┬────────┘  │  approval done)│
        │           └───────┬────────┘
        │                   │
        └─────────┬─────────┘
                  ▼
          ┌────────────────┐
          │ reporting_node │  ← always reached (success or failure)
          └───────┬────────┘
                  │
                  ▼
          ┌─────────────┐
          │    END()    │
          └─────────────┘
```

**Error path:** Any node can set `state.error` → conditional edge routes to `error_node` → END.

**Conditional edge functions:**

```python
def after_po_agent(state: SupplyChainGraphState) -> str:
    """Route after PO Agent: approval needed? → approval_node, else → inventory_node."""
    if state.error:
        return "error"
    if state.po_data and state.po_data.get("route") in ("approval_required_manager_a", "approval_required_manager_b"):
        return "approval"
    return "inventory"

def after_approval(state: SupplyChainGraphState) -> str:
    """After approval: if approved/rejected → inventory, if error → error."""
    if state.error:
        return "error"
    return "inventory"

def after_inventory(state: SupplyChainGraphState) -> str:
    """After inventory: always → reporting."""
    if state.error:
        return "error"
    return "reporting"

def after_reporting(state: SupplyChainGraphState) -> str:
    """Reporting is terminal → END."""
    return "end"  # or END constant
```

---

## 5. Checkpointing

- Use `SqliteSaver` from `langgraph.checkpoint.sqlite`.
- `thread_id = str(state.task_id)` for checkpoint scoping.
- Checkpoint DB path configurable via `settings.langgraph_checkpointer_db` (already in `packages/config/settings.py`).
- Resume: if graph crashes mid-execution, re-invoking with same `task_id` resumes from last checkpointed node.

---

## 6. Implementation Files

| File | Responsibility |
|------|---------------|
| `agents/supply_chain/graph.py` | `SupplyChainGraphState`, 5 node functions, `_build_supply_chain_graph()`, `SupplyChainGraphOrchestrator` |
| `docs/supply_chain/graph_design.md` | This design document |
| `tests/unit/test_supply_chain_graph.py` | Graph unit tests: happy path, approval path, auto-approve path, error path, checkpoint |

---

## 7. Integration with Existing Code

- Reuses existing `process_inbound_email()` from `agents/supply_chain/inbound.py`.
- Reuses existing `PurchaseOrderAgent`, `ApprovalWorkflow`, `InventoryMonitor`, `SupplyChainReporter`.
- Reuses existing guardrails wrappers (`po_guardrails.py`, etc.) — wrap agent calls with guardrails inside nodes.
- Uses existing `Settings.po_approval_thresholds` for routing decisions.
- Uses existing `MockLLMProvider` for testing (scripted responses).

---

## 8. Test Scenarios

### 8.1 Happy path — auto-approved PO
- Small PO ($50) → `po_agent_node` → route = `auto_approved` → skip approval → `inventory_node` → `reporting_node` → END.
- Assert: `final_result.status = "success"`, dashboard has correct PO metrics.

### 8.2 Approval path — PO requiring manager B approval
- Large PO ($6000) → `po_agent_node` → route = `approval_required_manager_b` → `approval_node` (stub auto-approve) → `inventory_node` → `reporting_node` → END.
- Assert: `approval_state = "approved"`, dashboard reflects approval.

### 8.3 Error path — invalid email content
- Email without PO data → `po_agent_node` fails → `error_node` → END.
- Assert: `final_result.status = "failed"`, error message present.

### 8.4 Checkpoint test
- Run graph with `task_id = UUID`, verify checkpoint DB has rows for each node transition.

---

## 9. Out of Scope (Deferred)

- **Langfuse tracing:** Deferred to later phase. Logging via Python logger for now.
- **Real human-in-the-loop approval:** Approval node currently stubs auto-approval. Real integration requires webhook/API for human decision — deferred.
- **Real inventory data:** Using mock data. Real inventory system integration deferred.
- **Gmail inbound trigger:** Handled by n8n workflow externally. Graph is invoked per-task, not as a long-running poll.

---

## 10. Relationship to Other Plans

- **Phase A Graph Orchestrator** (`plan-phaseA-graph-orchestrator.md`): General orchestrator graph for ALL agents. This supply chain graph is a **domain-specific workflow** that could either:
  - Run INSIDE the general orchestrator as a single agent step (i.e., `supply_chain.parse_po` capability triggers this sub-graph), OR
  - Run as a standalone graph for supply chain-only workflows.
- **Decision:** For now, implement as standalone `SupplyChainGraphOrchestrator` that can be invoked directly. Later, can be integrated into the general orchestrator as a sub-graph or agent capability.

---

## 11. Next Steps (after this design)

1. Implement `agents/supply_chain/graph.py` per this design.
2. Write `tests/unit/test_supply_chain_graph.py` with test scenarios above.
3. Run pytest → verify 10+ new tests pass + existing 225+ tests still pass.
4. Commit with `[verified]` prefix.
5. (Deferred) Add Langfuse tracing in later phase.
