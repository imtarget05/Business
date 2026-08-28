# Supply Chain Automation: LangGraph Agentic Pipeline Plan

## Overview
This document outlines the implementation roadmap for applying agentic AI principles to the existing Business Ops Agent Swarm supply chain components. The plan follows the user's suggested roadmap: LangGraph orchestration core + Harness Engineering checklist + phased implementation.

## 1. Current State Assessment

### What Exists (Pre-implemented)
| Component | File | Status | Tests |
|-----------|------|--------|-------|
| PO Agent | `agents/supply_chain/po_agent.py` | Implemented | 18 pass |
| Inbound Handler | `agents/supply_chain/inbound.py` | Implemented (stub for Gmail) | 22 + 15 integration |
| Approval Workflow | `agents/supply_chain/approval.py` | Implemented | 24 pass |
| Inventory Monitor | `agents/supply_chain/inventory.py` | Implemented | 27 pass |
| Reporting Agent | `agents/supply_chain/reporting.py` | Implemented | 27 pass |
| E2E Pipeline | `tests/unit/test_supply_chain_e2e.py` | Implemented | 10 pass |
| LangGraph Orchestrator | `packages/core/graph.py` | Implemented | (part of 225+ suite) |
| Ollama Provider | `packages/llm/ollama.py` | Implemented | (working with Qwen2.5 3B) |
| Docker Compose | `docker-compose.yml` | Configured (Ollama + API + Web) | 3 containers |

### What's Missing (Per User Roadmap)
1. **Harness Engineering checklist applied systematically** - Context, Tools+Guardrails, Evaluation, Langfuse not explicitly designed
2. **Graph design documentation** - No visual graph design before coding
3. **Langfuse tracing** - Not integrated yet
4. **Tool guardrails** - Existing tools have basic validation but no systematic guardrail framework

---

## 2. Roadmap Following User's Suggested Flow

### Phase 1: LangGraph as Orchestration Core (Enhanced)

**Current**: LangGraph exists in `packages/core/graph.py` for general orchestration  
**Goal**: Apply LangGraph specifically to supply chain agentic workflows with explicit graph design

**Steps**:
1. Review existing `packages/core/graph.py` - assess current LangGraph implementation
2. Document supply chain agent graph design (nodes, edges, state transitions)
3. Create dedicated supply chain LangGraph workflow (or enhance existing)
4. Define GraphState for supply chain context (PO data, approval state, inventory status, reporting context)

**Deliverables**:
- Graph design document (nodes, edges, state schema)
- Updated/enhanced LangGraph supply chain workflow
- GraphState type definition for supply chain context

---

### Phase 2: Harness Engineering Checklist

Apply the 4-part harness engineering framework from the user's reference:

#### 2.1 Context Design
**Question**: What does each agent need to know?

| Agent | Context Needed | Storage Mechanism |
|-------|---------------|------------------|
| PO Agent | PO email content, vendor info, item details | TaskRequest.payload |
| Approval Workflow | PO data, approval history, policy thresholds | ApprovalContext + Settings.po_approval_thresholds |
| Inventory Monitor | Current stock levels, reorder points, max levels | InventorySnapshot (in-memory) |
| Reporting Agent | PO processing history, approval decisions, inventory alerts | Mock data (placeholder for real data) |
| LangGraph Orchestrator | Full task state, agent handoffs, audit trail | GraphState + checkpoint |

**Implementation**:
- Enhance GraphState to carry supply chain context between nodes
- Add context validation in each agent's handle() method
- Document context requirements in agent docstrings

#### 2.2 Tools + Guardrails Design
**Question**: What can each agent do, and what's forbidden?

| Agent | Allowed Tools/Actions | Guardrails (Forbidden/Restricted) |
|-------|---------------------|-----------------------------------|
| PO Agent | Parse email content, classify PO type, route based on policy | Cannot modify external systems directly; cannot approve POs |
| Approval Workflow | Transition approval states, notify (stub), record decisions | Cannot auto-approve without human; cannot bypass timeout |
| Inventory Monitor | Read stock levels, generate alerts, compute summaries | Cannot modify inventory data (read-only monitoring) |
| Reporting Agent | Aggregate data, generate reports/dashboards | Cannot access real data without mock/placeholder; read-only aggregation |
| LangGraph Orchestrator | Route tasks, manage state, record audit trail | Cannot execute agents directly; delegates to registered handlers |

**Implementation**:
- Add explicit guardrail documentation in each agent
- Add input validation (already partially done in TaskRequest)
- Add permission/capability checks where appropriate
- Consider adding a Guardrail abstraction layer for systematic enforcement

#### 2.3 Evaluation Framework
**Question**: How do we measure success?

| Metric | Current Measurement | Target |
|--------|---------------------|--------|
| PO Parsing Success Rate | 18 unit tests pass | 100% on test cases |
| Classification Accuracy | Rule-based + LLM fallback | Measure on diverse PO formats |
| Approval Workflow Correctness | 24 unit tests pass | State transitions verified |
| Inventory Alert Accuracy | 27 unit tests pass | Correct alerts for all scenarios |
| E2E Pipeline Success | 10 e2e tests pass | Full pipeline works end-to-end |
| Latency | Not systematically measured | Add timing metrics |
| Cost | Not measured (local Ollama) | Track token usage if applicable |

**Implementation**:
- Add evaluation metrics to existing tests (success rate, latency tracking)
- Add performance benchmarks for each agent
- Document evaluation criteria in test files
- Consider adding evaluation dashboard/reports

#### 2.4 Langfuse Tracing Integration
**Question**: How do we observe agent behavior?

**Current**: logging via Python logger (packages/llm/ollama.py, agents use logging.getLogger)  
**Goal**: Integrate Langfuse for tracing, observability

**Steps**:
1. Research Langfuse integration options for LangGraph
2. Add Langfuse initialization (if applicable)
3. Instrument key agents with tracing spans
4. Configure tracing for development vs production

**Note**: Langfuse may require API keys; for local development, may use lightweight tracing or defer until production deployment needed.

**Deliverables**:
- Tracing design document
- Langfuse integration (or alternative tracing solution)
- Instrumented agents with trace spans

---

### Phase 3: Sequential Implementation

Following user's "luồng theo luồng tôi đề xuất" (flow according to my suggestion):

#### Step 1: Graph Design Documentation (Before Code)
1. Create `docs/supply_chain/graph_design.md`
2. Document each node (PO Agent, Approval, Inventory, Reporting) with:
   - Input state
   - Output state
   - Guardrails
   - Error handling
3. Document edges (transitions) with conditions
4. Review and approve graph design

#### Step 2: LangGraph Skeleton with One Simple Node
1. Create minimal LangGraph workflow with single node
2. Implement PO Agent as first node (already exists, integrate into graph)
3. Verify PO Agent runs through graph correctly
4. Add tests for graph-based PO processing

#### Step 3: Add Tool with Guardrails
1. Create tool abstraction for supply chain actions
2. Implement guardrails (permission checks, validation)
3. Add tool to PO Agent or Approval Workflow
4. Test tool with guardrails

#### Step 4: Attach Langfuse/Tracing
1. Research and setup tracing (Langfuse or alternative)
2. Add trace spans to key operations
3. Verify traces appear in output
4. Document tracing configuration

#### Step 5: Iterate and Enhance
1. Add remaining agents to graph (Approval, Inventory, Reporting)
2. Add edges/transitions between agents
3. Enhance evaluation metrics
4. Refine guardrails based on testing

---

## 3. Implementation Sequence (Detailed)

### Week 1: Foundation
- **Day 1**: Review existing LangGraph implementation, document current state
- **Day 2**: Create graph design document for supply chain workflow
- **Day 3**: Implement LangGraph skeleton with PO Agent node
- **Day 4**: Test graph-based PO processing, fix issues
- **Day 5**: Add basic evaluation metrics (success rate, latency)

### Week 2: Tooling & Guardrails
- **Day 1**: Design tool abstraction for supply chain actions
- **Day 2**: Implement guardrails framework
- **Day 3**: Add guardrails to PO Agent (input validation, permission checks)
- **Day 4**: Add guardrails to Approval Workflow (state transition validation)
- **Day 5**: Test guardrails, fix issues

### Week 3: Tracing & Observability
- **Day 1**: Research Langfuse/alternatives for LangGraph tracing
- **Day 2**: Setup tracing infrastructure
- **Day 3**: Instrument PO Agent with tracing
- **Day 4**: Instrument other agents with tracing
- **Day 5**: Verify tracing works, document configuration

### Week 4: Integration & Enhancement
- **Day 1**: Add Approval Workflow to graph
- **Day 2**: Add edges between PO Agent and Approval
- **Day 3**: Add Inventory Monitor to graph
- **Day 4**: Add Reporting Agent to graph
- **Day 5**: End-to-end testing, fix issues, finalize

---

## 4. Testing Strategy

### Existing Tests (Maintain)
- PO Agent: 18 unit tests (keep passing)
- Inbound Handler: 22 + 15 integration tests (keep passing)
- Approval Workflow: 24 unit tests (keep passing)
- Inventory Monitor: 27 unit tests (keep passing)
- Reporting Agent: 27 unit tests (keep passing)
- E2E Pipeline: 10 e2e tests (keep passing)

### New Tests to Add
1. **Graph-based PO processing tests** - Verify PO Agent runs through LangGraph correctly
2. **Guardrail enforcement tests** - Verify guardrails block unauthorized actions
3. **Tracing verification tests** - Verify traces are generated (if Langfuse integrated)
4. **Evaluation metric tests** - Verify metrics are calculated correctly

### Test Categories
| Category | Purpose | Examples |
|----------|---------|----------|
| Unit Tests | Test individual agents in isolation | PO parsing, approval state transitions, inventory alerts |
| Integration Tests | Test agent interactions | PO Agent → Approval Workflow handoff |
| E2E Tests | Test full pipeline | Inbound email → PO → Approval → Inventory → Reporting |
| Graph Tests | Test LangGraph workflow | Node execution, state transitions, edge conditions |
| Guardrail Tests | Test permission enforcement | Unauthorized actions blocked |
| Tracing Tests | Verify observability | Traces generated for key operations |

---

## 5. Stack Confirmation

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Orchestration | LangGraph | Already in codebase; explicit graph control; debuggable step-by-step |
| LLM Provider | Ollama (Qwen2.5 3B) | Local, no API costs, already configured, fast enough for supply chain tasks |
| Agent Framework | Custom DomainAgent protocol | Existing pattern, registry-driven routing, no need for CrewAI/other frameworks |
| Tracing | Langfuse (TBD) | Recommended by user; research integration with LangGraph |
| Testing | pytest + asyncio | Already in use, 225+ tests passing |
| Containerization | Docker Compose | Already configured, 3 containers (Ollama, API, Web) |

**Why not CrewAI**: LangGraph provides more control over workflow logic, easier debugging of complex supply chain processes (PO → approval → inventory → reporting), and is already in place.

**Why not other agent frameworks**: Existing DomainAgent protocol + registry pattern works well; adding another framework would add complexity without clear benefit.

---

## 6. Key Design Decisions

1. **LangGraph for supply chain orchestration**: Reuse existing LangGraph implementation, enhance with supply chain-specific graph design
2. **Harness Engineering applied systematically**: Apply context/tools/guardrails/evaluation framework to each agent
3. **Tracing integrated early**: Add Langfuse (or alternative) from start, not deferred to production
4. **Guardrails explicit**: Each agent has clear permissions and restrictions documented and enforced
5. **Evaluation metrics defined**: Success rate, latency, cost tracked per agent
6. **Graph design before code**: Document graph on paper first, then implement
7. **Incremental implementation**: One node at a time, test each, then add edges

---

## 7. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LangGraph complexity for simple workflows | Overengineering | Start with simple graph, add complexity only when needed |
| Langfuse integration issues | Tracing not working | Have fallback to standard logging; research Langfuse-LangGraph compatibility first |
| Guardrails too restrictive | Blocking legitimate operations | Design guardrails with flexibility; test thoroughly |
| Evaluation metrics overhead | Slowing down agents | Add metrics asynchronously; don't block agent execution |
| Ollama latency for complex tasks | Slow response times | Qwen2.5 3B is fast enough for supply chain tasks; monitor and adjust if needed |
| Test suite growth | Maintenance burden | Keep tests focused; remove obsolete tests; maintain 225+ suite |

---

## 8. Success Criteria

### Phase 1: LangGraph Core
- [ ] Supply chain graph design documented
- [ ] LangGraph skeleton with PO Agent runs correctly
- [ ] GraphState carries supply chain context properly

### Phase 2: Harness Engineering
- [ ] Context requirements documented for each agent
- [ ] Tools + guardrails designed and implemented
- [ ] Evaluation metrics defined and measured
- [ ] Tracing integrated (Langfuse or alternative)

### Phase 3: Full Pipeline
- [ ] All supply chain agents integrated into LangGraph workflow
- [ ] End-to-end pipeline works (inbound → PO → approval → inventory → reporting)
- [ ] All existing tests still pass (133+ tests)
- [ ] New tests added for graph, guardrails, tracing

### Overall Success
- [ ] Supply chain automation pipeline functional
- [ ] Observable via tracing
- [ ] Guardrails enforce permissions
- [ ] Evaluation metrics show success
- [ ] Can demonstrate to internship evaluators

---

## 9. Next Steps (Immediate)

1. **Review existing LangGraph implementation** (`packages/core/graph.py`) - understand current state
2. **Create graph design document** for supply chain workflow
3. **Discuss Langfuse integration** - research options, decide on approach
4. **Prioritize implementation** - which phase to start first based on user's timeline (before Tet/2/9)

---

## 10. Questions for User

1. Should I start with reviewing existing LangGraph implementation, or jump directly to graph design for supply chain?
2. Is Langfuse tracing required now, or can it be deferred? (Requires API keys, may be overkill for local development)
3. What's the priority order: Graph design → LangGraph skeleton → Tool guardrails → Tracing? Or different order?
4. Any specific supply chain workflow you want to prioritize beyond PO processing?
5. Timeline: Still targeting before Tet (2/9)? What's the realistic deadline for this plan?

---

*Document Version: 1.0*  
*Last Updated: Based on user's suggested roadmap and current codebase state*  
*Author: AI Assistant (following user's suggested flow)*