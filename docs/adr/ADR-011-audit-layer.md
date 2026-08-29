# ADR-011: Centralized Audit Layer

## Status
Accepted

## Context
Logging answered runtime/debugging questions only. There was no immutable trail
answering "who did what, to which target, under which policy, with which result".

## Decision
- `packages/core/audit.py` exposes `AuditService.emit(...)` emitting to the
  pre-existing `audit_logs` table (append-only; no UPDATE/DELETE).
- Closed event vocabulary: `task_created, task_assigned, agent_selected,
  agent_started, tool_invoked, policy_evaluated, approval_requested/granted/denied,
  action_executed, action_failed, retry, handoff, agent_result, reviewer_result,
  task_completed, task_failed` (ADR-009 doc list).
- Risk classification: `READ/WRITE/DESTRUCTIVE` derived from capability via
  `classify_risk()`. DESTRUCTIVE capability string fragments trigger
  `APPROVAL_REQUESTED` before execution.
- Secret redaction applied to every emitted `payload` (`_SECRET_PATTERN`).
- Audit failures are caught and logged; audit NEVER breaks the task pipeline.

## Consequences
- Compliance/root-cause queries read from a single append-only source.
- InMemoryAuditService available for tests.
