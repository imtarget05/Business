# Agent Flow

## Lifecycle mapping

```text
TaskStatus (contract)        Orchestrator stage
-------------------------    --------------------------------------
PENDING                      task accepted, persisted
CLASSIFYING                  LLM classification via LLMProvider
ROUTING                      registry.get_by_capability(domain.action)
RUNNING                      agent.handle(request) under timeout
VALIDATING                   output validation (citations, non-empty result)
COMPLETED / FAILED /
ESCALATED                    terminal states (see state machine)
```

## Routing

Routing is **registry-driven**: each `AgentDescriptor` advertises capability
strings (e.g. `knowledge.query`, `support.triage`). The orchestrator resolves
`{domain}.{action}` through the registry — there is no domain if/else in core
routing code. Registering a new agent automatically makes it routable.

## Error handling

- Agent timeout → `AGENT_TIMEOUT` (504), response status `timeout`.
- Unknown capability → `AGENT_NOT_FOUND` (404).
- Agent-level rejection → response status `rejected` (still a completed task
  lifecycle: the *task* succeeded in being processed deterministically).
- All failures return the standard error envelope with `task_id`.

## Audit trail

Every delegation writes an `agent_runs` row (attempt, timings, error) and an
append-only `audit_logs` entry. Phase 1 wires persistence into the flow above;
Phase 0 defines the shapes and the state machine.
