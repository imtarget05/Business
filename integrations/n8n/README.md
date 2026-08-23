# n8n Integration Boundary (Phase 0)

n8n is an **inbound integration layer only** — see ADR-003.

## Contract

```text
Webhook / Slack / Gmail / Cron
        ↓  (n8n workflow)
POST {API_BASE_URL}/v1/tasks
Headers: X-Request-ID, X-Trace-ID, Authorization: Bearer <service-token>
Body:    TaskRequest contract (packages/contracts/models.py)
```

## Rules for any future n8n workflow

1. Nodes may only transform payloads into a valid `TaskRequest` and call the
   API. No reasoning, no prompts, no routing decisions.
2. Every execution must set `X-Request-ID` (or accept the response header) so
   runs are traceable in audit logs.
3. Workflow JSON exports belong in this directory once created.

Phase 0 ships no workflows by design.
