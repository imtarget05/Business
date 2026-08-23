# ADR-006: Dashboard as Control Plane

## Context

The dashboard must show tasks, agent runs, evaluation metrics and audit logs.
There is a risk of the UI accumulating routing/business logic (e.g. choosing
which agent handles what), which would create a second source of truth and
break the single-orchestrator principle.

## Decision

The Next.js dashboard is strictly a **control plane / observability surface**:

1. It calls only the Business Ops API (`/v1/*`, `/health`, `/ready`).
2. It never decides agent routing — routing is exclusively the backend
   orchestrator's job via the registry.
3. It holds no business rules; all displayed state originates from the API or
   database through the API.
4. It is designed as a production operations UI (dense tables, timelines,
   filters), not a marketing site.

## Alternatives considered

1. **Server-side rendering with direct DB access from the dashboard** —
   rejected: bypasses auth boundary and audit; couples schema to UI.
2. **Separate admin service per domain** — rejected for now: unnecessary
   fragmentation at this scale; one coherent shell scales to Phase 5.
3. **Retool/grafana-only** — rejected: insufficient for knowledge management
   and task workflows we need later.

## Consequences

- ✅ Single enforcement point for authorization, validation and audit (API).
- ✅ Frontend stays thin; can be rebuilt without touching core logic.
- ⚠️ Every new dashboard capability requires an API endpoint first — slightly
  slower initial iteration, but keeps the architecture honest.
