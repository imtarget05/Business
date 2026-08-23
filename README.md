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

\* Ollama is an **optional** provider only. The system never requires a local
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

### Backend

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

### Frontend

```bash
cd apps/web
npm install
npm run dev    # http://localhost:3000
```

### Database migrations (requires a PostgreSQL with pgvector)

```bash
alembic upgrade head
```

Local development can use the compose `pgvector/pgvector:pg16` container or a
Neon connection string directly.

### Docker

```bash
docker compose up --build
# api  -> http://localhost:8000/docs
# web  -> http://localhost:3000
```

No GPU, no model downloads, no Ollama service in the stack.

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
