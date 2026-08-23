# n8n Integration Layer

n8n is an **inbound integration layer only** — see ADR-003.

## Workflow: Inbound Task Relay (`inbound-task-relay.json`)

Phase 1 ships the first real workflow. Flow:

```text
External source (Slack / Gmail / Cron / custom webhook)
        ↓
  Webhook Trigger (POST /business-ops-relay)
        ↓
  HTTP Request → POST {BUSINESS_OPS_API_URL}/v1/tasks
        ↓
  IF response.status == "escalated"
   ├── YES → Slack Webhook alert with task_id + dashboard link
   └── NO  → No action (task completed normally)
```

### Environment variables (set in n8n)

| Variable | Purpose |
|---|---|
| `BUSINESS_OPS_API_URL` | Base URL of the API (default: `http://host.docker.internal:8000`) |
| `BUSINESS_OPS_API_KEY` | Value for `X-API-Key` header (must match `.env` `API_KEY`) |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook for escalation alerts |
| `DASHBOARD_URL` | Dashboard base URL for deep links (default: `http://localhost:3000`) |

### Import

1. Open n8n UI → Workflows → Import from File
2. Select `integrations/n8n/inbound-task-relay.json`
3. Set environment variables in n8n Settings → Variables
4. Activate the workflow

### Contract

```text
Webhook / Slack / Gmail / Cron
        ↓  (n8n workflow)
POST {API_BASE_URL}/v1/tasks
Headers: X-API-Key, X-Request-ID
Body:    TaskRequest contract (packages/contracts/models.py)
```

### Rules for any n8n workflow

1. Nodes may only transform payloads into a valid `TaskRequest` and call the
   API. No reasoning, no prompts, no routing decisions.
2. Every execution must set `X-Request-ID` (or accept the response header) so
   runs are traceable in audit logs.
3. Workflow JSON exports belong in this directory for version control.
