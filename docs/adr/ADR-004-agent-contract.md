# ADR-004: Typed Agent Contract

## Context

The orchestrator delegates work to specialized agents. Without a strict,
versioned contract every new agent risks ad-hoc request/response shapes,
breaking the orchestrator or dashboard silently.

## Decision

All inter-agent communication uses typed Pydantic models in
`packages/contracts`:

- **TaskRequest**: `task_id`, `domain`, validated `action`, typed `payload`,
  `TaskContext` (channel, actor, correlation), `metadata`.
- **AgentResponse**: `task_id`, agent qualified name, status ∈ {success,
  failed, rejected, escalated, timeout}, typed `result`, `citations`,
  bounded `confidence`, optional `ErrorDetail`.
  - `success` responses may not carry an error; failure-family statuses must.
- **AgentDescriptor**: registry entry — id/name/domain/version/capabilities/
  status/timeout_ms/max_retries, with capability strings validated against
  their domain prefix.

Constraints:

1. No blanket `dict[str, Any]` payloads where structure is knowable.
2. Contracts are additive-evolution only within a major version.
3. Every API error response carries `task_id`/correlation ID.

## Alternatives considered

1. **Free-form dicts** — rejected: no validation, no editor support, runtime
   surprises at orchestrator boundaries.
2. **gRPC/protobuf contracts** — rejected for Phase 0: heavier toolchain;
   Pydantic models already give runtime validation and JSON-native HTTP flow.
3. **JSON Schema files without codegen** — rejected: duplicates truth vs.
   Pydantic models that both validate and document.

## Consequences

- ✅ Orchestrator/dashboard can rely on stable shapes; contract tests enforce.
- ✅ New agents integrate by conforming to the contract, not by editing core.
- ⚠️ Evolving payloads requires discipline (optional fields + defaults).
