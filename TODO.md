# TODO / Roadmap

## Housekeeping

- [x] Remove runtime logs (`*.log`, `pytest_out.txt`) from version control
- [x] Remove job-search runtime data (`job_audit_log.json`, `job_search_results.json`,
      `verified_jobs.json`, `contacts_backup.json`) from version control
- [x] Remove internal working notes / agent-generated reports
      (`task-2-report.md`, `task-5-report.md`, `implementation_plan.md`,
      `.hermes/`, `.superpowers/`) from version control
- [x] Update `.gitignore` to permanently block the above file patterns
- [x] Rewrite `README.md` — remove outdated internal context, add Roadmap section

## Architecture & Scalability

- [ ] Replace in-memory rate limiter with Redis (per-process limiter breaks under
      multi-worker deployment)
- [ ] Add a message queue (ARQ/Celery) for async task execution and backpressure
- [ ] Move agent registry to DB-backed storage for multi-worker consistency
- [ ] Distributed tracing propagation (W3C Trace Context / OTel) across the API boundary

## Reliability & Safety

- [ ] Orchestrator-level circuit breaker (extend the supply-chain `CircuitBreaker`)
- [ ] Define SLIs (latency p99, error rate) → SLOs → error budgets
- [ ] ML-based prompt-injection detection (replace regex-only patterns)

## Product / Features

- [ ] Multi-tenant isolation in vector search (row-level security or per-org collections)
- [ ] A/B testing framework for prompt/model variants
- [ ] Abstract embedding dimension so provider changes don't require a migration
