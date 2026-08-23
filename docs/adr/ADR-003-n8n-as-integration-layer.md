# ADR-003: n8n as Integration Layer Only

## Context

Inbound business triggers arrive from Slack, Gmail, cron schedules and
webhooks. n8n excels at visually wiring these sources, but putting reasoning
or business rules into workflow nodes would scatter core logic outside
version-controlled code and make testing nearly impossible.

## Decision

n8n is an **inbound integration/automation layer only**:

```text
Webhook / Slack / Gmail / Cron → n8n → POST Business Ops API /v1/tasks
```

Hard rules:

1. All orchestrator reasoning, prompts and routing live in Python code.
2. No critical prompt or business rule may be hard-coded in an n8n workflow.
3. n8n authenticates to the API as a service actor; every call carries a
   correlation ID.
4. Phase 0 defines this boundary only; no full n8n workflows are shipped.

## Alternatives considered

1. **Orchestrator implemented in n8n nodes** — rejected: untestable, opaque,
   un-versionable business logic.
2. **Direct webhooks into FastAPI without n8n** — viable later, but loses the
   low-code connector ecosystem; boundary keeps this optional.
3. **Temporal/Celery pipelines** — heavier than needed; revisit if long-running
   orchestration demands it.

## Consequences

- ✅ Core logic stays testable and reviewable in one codebase.
- ✅ Non-engineers can add trigger integrations without touching core code.
- ⚠️ n8n workflow definitions must be treated as disposable glue, not source of
  truth; export them to `integrations/n8n/` once they exist.
