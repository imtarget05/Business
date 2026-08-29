# ARCHITECTURE AUDIT — Hermes Agentic / Business Ops Agent Swarm

## 1. Current Architecture Summary

Stack: FastAPI (apps/api) -> packages/core (Orchestrator / GraphOrchestrator / RouterAgent /
InMemoryAgentRegistry / policy / persistence) -> agents/* (domain) -> packages/llm,
packages/database (pgvector), packages/tools, packages/observability. Telegram bot + n8n
are gateways only. Deploy: docker-compose (dev), Neon Postgres target for prod.

Existing execution lifecycle (packages/contracts/state_machine.py):
PENDING -> CLASSIFYING -> ROUTING -> RUNNING -> VALIDATING -> SUCCEEDED/FAILED/DEAD_LETTERED,
with handoff chain (depth limit + cycle detection), policy check per hop, retry (2 attempts
on transient errors, centralized in orchestrator/graph — NOT copy-pasted into agents).

Routing: registry-driven by capability string "domain.action"; RouterAgent infers intent
from free text via LLM structured classification + deterministic keyword fallback + escalation.

Observability: structured logging (packages/observability/logging.py), request context
(trace/request/task ids, context.py), tracers (Langfuse / OTel, core/tracing.py).
NO metrics module. NO audit layer. NO evidence store.

## 2. Agent Inventory (existing)

| Agent | Location | Capabilities | Status |
|---|---|---|---|
| RouterAgent | packages/core/router.py | intent classification | Mature |
| Orchestrator / GraphOrchestrator | packages/core/orchestrator.py, graph.py | task lifecycle, routing, handoff, retry, dead-letter | Mature |
| KnowledgeAgent (RAG) | agents/knowledge | knowledge.query/draft/ingest | Mature (pgvector) |
| SupportAgent | agents/support | support.triage/draft_reply/escalate | Mature |
| ReportingAgent | agents/reporting | report.generate | Mature |
| ResearchAgent | agents/research | research.web_search/arxiv_search/summarize | Mature (ADR-008 web tools) |
| YoutubeAgent | agents/youtube | youtube.search/transcript/summarize | Mature |
| GmailAgent | agents/gmail | gmail.list/search/send/draft | Mature |
| CalendarAgent | agents/calendar | calendar.list/create/delete_events | Mature |
| ContextAgent (memory) | agents/context | context.get/append/summarize/clear | Mature |
| PurchaseOrderAgent | agents/supply_chain | supply_chain PO parsing/routing | Mature |
| InventoryMonitor | agents/supply_chain | check_inventory/get_alerts/get_summary | Mature |
| SupplyChainReporter | agents/supply_chain | generate_report/get_dashboard/... | Mature |
| Telegram gateway | agents/monitoring/telegram_bot.py | chat interface | Gateway (decoupled) |
| Scheduler | agents/monitoring/scheduler.py | periodic jobs | Operational |
| Input filter (planned) | packages/core/input_filter.py | sanitize pre-LLM | Per approved plan |
| Learning loop (planned) | packages/core/learning.py | feedback-driven rule updates | Per approved plan |

Duplication check: no duplicate routing logic (single registry); no duplicate lifecycle;
no copy-paste retry/logging inside agents. Filter layer + learning loop already approved.

## 3. Capability Matrix — 60 Target Roles vs Existing System

Legend: UPGRADE = existing agent covers, extend it. SERVICE = deterministic tool/service,
not an LLM agent. CREATE = genuinely missing, safe to add. DEFER = would be technical debt
in a business-ops platform; do not implement now.

### Orchestration (1-8)
| # | Role | Existing? | Action | Reason |
|---|---|---|---|---|
| 1 | Router | Yes (RouterAgent) | UPGRADE | Add capability-score matching + dynamic rules (learning loop) |
| 2 | Planner | No | UPGRADE Orchestrator/graph | Orchestrator already builds execution chain (handoff DAG); add explicit plan step, not a new agent |
| 3 | Task Manager | Yes (Orchestrator + TaskStore + state machine) | UPGRADE | Already owns lifecycle; do not create second lifecycle |
| 4 | Dispatcher | Yes (route_node + registry) | UPGRADE | Registry-driven dispatch exists |
| 5 | Handoff | Yes (handoff_node, depth+cycle detection) | KEEP | Mature |
| 6 | Reviewer | Partial (validate_node) | UPGRADE | Extend validation; add ReflectionEngine (approved plan) |
| 7 | Conflict Resolver | No | DEFER | No multi-writer contention in current domain |
| 8 | Priority | No | SERVICE | Priority = queue policy (deterministic), not LLM |

### Software Development (9-16)
| 9-16 | Code/Frontend/Backend/API/DB/Refactor/Dependency/Documentation | No | DEFER | Platform is business-ops, not a coding platform. Creating these = pure debt. Doc generation = SERVICE (reporting agent already generates reports) |

### Testing & Quality (17-24)
| 17-24 | Test Planner/Unit/Integration/E2E/Perf/Chaos/Regression/Test Analysis | No | DEFER | Testing is a CI concern; pytest suite + ruff already gate quality. Deterministic, not LLM-agent material |

### DevOps / CI-CD (25-32)
| 25 | Git | No | SERVICE | Deterministic CLI operations |
| 26 | CI | No | SERVICE | Already covered by test/ruff gates + compose |
| 27 | CD | No | SERVICE | compose prod + migration entrypoint (approved plan) |
| 28 | Docker | No | SERVICE | Dockerfiles/compose already exist |
| 29-32 | K8s/Helm/Infra/Release | No | DEFER | No k8s cluster in deployment target (compose + Neon). Revisit if/when migrating to k8s |

### Cloud / Distributed (33-40)
| 33 | AWS | No | DEFER | Deployment target is Neon + compose |
| 34-35 | Azure/GCP | No | DEFER | Same |
| 36-38 | Network/Service Mesh/Distributed | No | DEFER | Single-service runtime; mesh is N/A |
| 39 | Storage | Partial (DB repositories) | UPGRADE | Extend repositories, not a new agent |
| 40 | Cloud Cost | No | DEFER | No multi-cloud spend to manage |

### Security / Supply Chain (41-48)
| 41 | Security | Partial (auth middleware, policy checker, allowlists) | UPGRADE | Centralize policy + approval boundary (risk levels) |
| 42 | SAST | No | SERVICE | ruff/bandit in CI, deterministic |
| 43 | Dependency Security | No | SERVICE | pip-audit in CI |
| 44 | Secret | Partial (.env, fail-closed auth) | UPGRADE | Secret redaction in audit/logs |
| 45-46 | Container/K8s Security | No | DEFER | No k8s |
| 47 | Supply Chain (procurement) | Yes (3 supply_chain agents) | KEEP | Already the strongest domain |
| 48 | Compliance | No | UPGRADE -> Audit layer | Compliance = audit trail + policy enforcement, not an LLM agent |

### Observability / Ops (49-55)
| 49 | Monitoring | Partial (InventoryMonitor, scheduler, health) | UPGRADE | Add metrics module (packages/observability/metrics.py) |
| 50 | Logging | Yes (observability/logging.py) | UPGRADE | Add correlation fields (trace_id/task_id/execution_id/agent_id) standard |
| 51 | Tracing | Yes (Langfuse/OTel tracers) | KEEP | Reuse, do not build second system |
| 52 | Incident | Partial (dead-letter + telegram alerts) | UPGRADE | Dead-letter -> telegram notification wiring |
| 53 | Root Cause | No | CREATE (later phase) | LLM analysis over audit + logs; only valuable AFTER audit layer exists |
| 54 | SRE | No | DEFER | Umbrella role, covered by 49-53 |
| 55 | Recovery | Partial (retry + dead-letter + rollback in TaskStore) | UPGRADE | Recovery playbook hooks |

### Knowledge / AI / Evidence (56-60)
| 56 | Research | Yes (ResearchAgent) | KEEP | Mature (ADR-008) |
| 57 | RAG | Yes (KnowledgeAgent + pgvector) | KEEP | Mature |
| 58 | Memory | Yes (ContextAgent) | UPGRADE | Wire into orchestrator for cross-task context |
| 59 | Evidence | No | CREATE | Evidence contract + store on AgentResponse (findings + citations + raw data refs) |
| 60 | Audit | No | CREATE | Centralized append-only audit layer (deterministic service, NOT LLM) |

## 4. Duplication / Overlap Report

- Router vs Registry routing: complementary (intent inference vs capability resolution),
  documented in docs/architecture/router-vs-registry.md. No merge needed.
- InventoryMonitor vs Monitoring (role 49): InventoryMonitor is a business-domain agent;
  platform monitoring is observability infra. Boundary clear, no merge.
- ReportingAgent vs SupplyChainReporter: different domains (general ops vs supply_chain).
  Overlap is in report formatting -> extract shared report utilities to packages/, keep agents.
- Security (41) vs Secret (44) vs Compliance (48): boundaries set — 41 = policy/approval
  orchestration; 44 = redaction/secret hygiene in audit+logs; 48 = audit trail consumer.
- Duplication found and already fixed earlier: agents/monitoring/research.py removed
  (dead code shadowed by package); hermes fallbacks unified into packages/tools (ADR-008).

## 5. Proposed Topology (phased)

PHASE 1 — Foundation (do first, unblocks everything):
1. Audit layer: packages/core/audit.py — append-only AuditLog (DB table + interface),
   emits events: task_created/assigned/agent_selected/started/tool_invoked/policy_evaluated/
   approval_requested/granted/denied/action_executed/action_failed/retry/handoff/agent_result/
   reviewer_result/task_completed/task_failed. Risk levels READ/WRITE/DESTRUCTIVE on
   capabilities; destructive -> approval gate via PolicyChecker. Secret redaction.
2. Structured logging upgrade: standard correlation fields (trace_id, task_id,
   execution_id, agent_id, parent_execution_id) auto-injected from request context.
3. Metrics module: counters/histograms (executions, success/failure rate, latency,
   retries, routing decisions, queue latency, token usage).
4. Capability-based routing: extend RouterAgent — score candidate agents by capability
   overlap from registry descriptors (not just exact capability string), policy check,
   then select. Keep rule fallback + escalation.

PHASE 2 — Approved production plan (implementation_plan.md, already confirmed):
Input filter layer, learning loop, feedback API, reflection, production compose.

PHASE 3 — Selective new agents (only after Phase 1+2 stable):
- Planner step: explicit plan node in graph (DAG of minimal required agents; never
  broadcast to all agents — only activate required ones).
- Root Cause Agent (LLM over audit+metrics evidence).
- Evidence enrichment on AgentResponse (findings + citations + data refs), Reviewer
  upgrade, Memory wiring, Recovery playbooks, Incident->Telegram wiring.

NOT CREATED (intentionally): 25 SWE/testing/cloud agents (roles 9-24, 25-32 subset,
33-40) — they are deterministic services or out-of-scope for a business-ops platform;
creating them would be pure technical debt. They stay SERVICE/DEFER per matrix above.

## 7. Audit — Implementation Status
DONE (Phase 1 delivered): AuditService + InMemoryAuditService emit to pre-existing
`audit_logs` table (models.py::AuditLog, append-only). Wire audit hooks into
Orchestrator.execute (task_created/completed/failed/retry) + bootstrap injection.
AuditService, LearningEngine, ReflectionEngine exposed via AppContainer.

- InMemoryAgentRegistry is per-process; multi-worker prod needs shared registry source
  (DB-backed descriptor sync) before scaling beyond 1 API worker.
- Approval workflow (DESTRUCTIVE ops) needs a human-in-loop channel (Telegram) — design
  in Phase 1 but enforcement optional per-capability.
- Do NOT implement Phase 3 agents before audit+capability routing exist (dependency order).
