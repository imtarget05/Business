# Architecture Overview

## Components

| Component | Technology | Responsibility |
| --- | --- | --- |
| Dashboard | Next.js 15, TypeScript, Tailwind CSS v4 | Task/agent/run monitoring, audit views. Control plane only — calls the API, never routes tasks. |
| Backend / API Gateway | FastAPI, Pydantic v2 | Auth boundary, task lifecycle, orchestrator, agent registry, validation, audit, observability, LLM abstraction. |
| Domain Agents | Python (`agents/knowledge`, `agents/support`) | Specialized capability execution behind a uniform `DomainAgent` protocol. |
| Database | Neon PostgreSQL + pgvector | System of record: organizations, users, agents, tasks, runs, audit logs, documents/chunks (vector), evaluations. |
| LLM Layer | `packages/llm` provider abstraction | mock / Cloudflare AI / OpenAI-compatible / Ollama (optional). |
| Integrations | n8n | Inbound webhooks, Slack/Gmail/cron triggers → Business Ops API. No business logic in nodes. |

## Request flow

```text
Client/n8n → POST /v1/tasks
  → FastAPI middleware assigns request_id / propagates trace_id
  → Orchestrator.execute(TaskRequest)
      1. CLASSIFYING   — LLM-assisted classification (abstraction call)
      2. ROUTING       — registry lookup by capability string
      3. RUNNING       — agent.handle() under descriptor.timeout_ms
      4. VALIDATING    — output checks (citations required for knowledge)
      5. terminal      — COMPLETED / FAILED / ESCALATED
  → AgentResponse (typed contract) returned with correlation IDs
```

## Layering rules

- `contracts` has no dependencies on other packages.
- `core` depends on `contracts`, `observability` and receives an `LLMProvider`
  via injection — it never imports provider SDKs.
- `database` is only touched by API/service layer code; agents receive plain
  contract models.
- Provider-specific config (Cloudflare account IDs, etc.) lives in
  `packages/config` + `infrastructure/`, never in domain logic.

## Extension points

- **New domain agent**: implement `DomainAgent`, register in
  `packages/core/bootstrap.py`. Zero orchestrator changes.
- **New LLM provider**: implement the `LLMProvider` protocol and add a factory
  branch. Business code unchanged.
- **New external trigger**: add n8n workflow calling `/v1/tasks` with a service
  credential; no core changes.
