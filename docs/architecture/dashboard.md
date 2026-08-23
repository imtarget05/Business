# Dashboard

The Next.js dashboard is the **control plane** for observing and managing the
agent swarm. It never decides agent routing and never talks to agents or the
LLM directly — it calls the Business Ops API only.

## Routes (Phase 0 shell)

| Route | Purpose | Live data |
| --- | --- | --- |
| `/dashboard` | System overview | Phase 1 |
| `/tasks` | Task lifecycle monitor | Phase 1 |
| `/agents` | Registry: agents, versions, capabilities, status | Phase 1 |
| `/runs` | Agent run timeline (attempts, durations, errors) | Phase 1 |
| `/knowledge` | Document management & ingestion status | Phase 2 |
| `/evaluation` | Evaluation metrics (no fake data is shown) | Phase 5 |
| `/audit` | Append-only audit trail viewer | Phase 1 |

## Principles

1. Read/write exclusively through `/v1/*` API endpoints.
2. All state displayed comes from the backend; the dashboard holds no routing
   or business rules.
3. `NEXT_PUBLIC_API_BASE_URL` is the only frontend configuration knob.
4. Production application UI (dense tables, filters, timelines) — not a
   marketing landing page.
