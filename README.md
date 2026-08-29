# Business Ops Agent Swarm

Multi-agent platform for business operations. Phase 0 delivers the
**architectural foundation**: contracts, database schema, LLM abstraction,
orchestrator skeleton, dashboard shell — no business feature logic.

## Architecture at a glance

```text
Next.js Dashboard ──> FastAPI Backend ──> Orchestrator
                                            ├── Agent Registry (capability routing)
                                            ├── Knowledge Agent   (Phase 2)
                                            └── Support Agent     (Phase 3)
                                                   │
                                    Neon PostgreSQL + pgvector
                                                   │
                                        LLM Provider abstraction
                                  (mock | cloudflare_ai | openai-compatible | ollama*)

External: Slack / Gmail / Cron / Webhook → n8n → Business Ops API
```

* Ollama is an **optional** provider only. The system never requires a local
LLM (see [ADR-001](docs/adr/ADR-001-no-local-llm.md)).

## Repository layout

| Path | Purpose |
| --- | --- |
| `apps/api` | FastAPI backend (orchestrator, health, task endpoints) |
| `apps/web` | Next.js + Tailwind dashboard shell |
| `agents/` | Domain agents (`knowledge`, `support`) |
| `packages/contracts` | Typed Pydantic request/response/state models |
| `packages/core` | Orchestrator, registry, errors, agent protocol |
| `packages/database` | SQLAlchemy models, session, Alembic migrations |
| `packages/llm` | LLMProvider abstraction + implementations |
| `packages/config` | Typed settings (.env-driven) |
| `packages/observability` | Structured logging + correlation context |
| `infrastructure/docker` | Dockerfiles for api & web |
| `integrations/n8n` | n8n boundary notes (integration layer only) |
| `docs/adr` | Architecture Decision Records |

## Quick start

### Docker (recommended for local development)

```bash
# 1. Copy env template and adjust if needed
copy .env.example .env           # Windows
# cp .env.example .env           # Linux/macOS

# 2. Start all services (API, Web, Postgres+pgvector, n8n)
docker compose up --build -d

# 3. Seed demo data (pilot org, customer, knowledge doc, conversation)
docker compose exec api python scripts/seed_demo.py

# 4. Open services
#    API docs:     http://localhost:8000/docs
#    Dashboard:    http://localhost:3000
#    n8n UI:       http://localhost:5678  (admin/admin by default)
```

### Import n8n workflows

1. Open n8n UI at `http://localhost:5678` → Workflows → **Import from File**
2. Select `integrations/n8n/inbound-task-relay.json` (and `gmail-inbound-reply.json` if needed)
3. Set environment variables in n8n **Settings → Variables**:
   - `BUSINESS_OPS_API_URL` = `http://api:8000`
   - `BUSINESS_OPS_API_KEY` = value from `.env` `API_KEY`
   - `SLACK_WEBHOOK_URL` = your Slack incoming webhook (optional)
   - `DASHBOARD_URL` = `http://localhost:3000`
4. For Gmail workflow: also set `BASE_URL`, `API_KEY`, `GMAIL_SENDER_EMAIL` and
   configure Gmail OAuth2 credentials (see `integrations/n8n/README.md`)
5. **Activate** the workflow(s)

### Local development (without Docker)

#### Backend

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
copy .env.example .env           # defaults work with mock LLM + docker db
uvicorn apps.api.main:app --reload
```

- `GET /health` — liveness (no DB, no LLM calls)
- `GET /ready` — DB connectivity + config checks
- `POST /v1/tasks` — execute a task through the orchestrator
- `GET /v1/agents` — list registered agents and capabilities

Example:

```bash
curl -X POST http://localhost:8000/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"domain":"knowledge","action":"query","payload":{"question":"Hi"}}'
```

With `LLM_PROVIDER=mock` this works with zero credentials and zero network.

#### Frontend

```bash
cd apps/web
npm install
npm run dev    # http://localhost:3000
```

#### Database migrations (requires a PostgreSQL with pgvector)

```bash
alembic upgrade head
```

Local development can use the compose `pgvector/pgvector:pg16` container or a
Neon connection string directly.

## Tests & quality gates

```bash
pytest              # unit + API integration tests (no DB required)
ruff check .        # lint
python -m compileall packages agents apps migrations
cd apps/web && npm run build   # type-checks + builds the shell
```

## Key constraints (enforced by ADRs)

1. **No local LLM requirement** — Ollama is optional; default is `mock`.
2. **Neon PostgreSQL + pgvector** — no Supabase anywhere.
3. **n8n is integration-only** — no business logic in workflow nodes.
4. **Registry-driven routing** — no `if domain == "support"` in core code.
5. **All LLM calls via `LLMProvider`** — business logic never touches SDKs.
6. **Dashboard is a control plane** — it calls the API; it never routes tasks.

## Production (24/7)

```bash
# production stack (no internal Ollama; uses cloud LLM per .env)
docker compose -f docker-compose.prod.yml up -d --build
# entrypoint auto-runs alembic migrations then serves uvicorn x2 workers
```

### Observability & safety (Phase 1)
- **Input Filter Layer** (`packages/core/input_filter.py`): sanitizes every raw text
  payload *before* any LLM call — length cap, spam/empty detection,
  prompt-injection detection, PII masking (email/VN phone/CCCD). Spam or injection
  inputs short-circuit as `REJECTED`, never hitting the LLM.
- **Audit layer** (`packages/core/audit.py`): append-only events into the existing
  `audit_logs` table. Risk classification `READ/WRITE/DESTRUCTIVE` on capabilities.
  Audit failures never break the task pipeline; secret payloads are redacted.
- **Metrics** (`packages/observability/metrics.py`): in-process counters/timers
  exported via `/health`; Prometheus-swap sink later with no call-site changes.
- **Capability-based routing** (`RouterAgent.candidates`): ranks registered agents
  by keyword/capability overlap — the Planner picks a *minimal* agent chain,
  never broadcasts to all agents.

### Learning loop (Phase 2)
```bash
POST /v1/feedback      # rating up/down + corrected_capability + comment
GET  /v1/feedback/stats
```
- `LearningEngine` turns corrections into `dynamic_rules` consumed by `RouterAgent`
  *before* static keyword fallbacks — routing improves from user feedback.
- `ReflectionEngine` auto-critiques agent output (fire-and-forget) to seed ratings.
- Daily `Run learning cycle` scheduler job reports totals to Telegram.

### New agents (Phase 3)
- **Root Cause Agent** (`agents/root_cause`, `ops.root_cause` / `ops.get_metrics`):
  LLM analysis over Audit events + Metrics — evidence-first, refuses to guess without
  data. Registered only after audit+metrics layers exist.

> 25 of the original 60 target roles (SWE/testing/cloud) are intentionally **not**
> implemented as LLM agents — see `docs/audit/ARCHITECTURE_AUDIT.md`. In a
> business-ops platform they remain deterministic services or deferred (pure debt).
