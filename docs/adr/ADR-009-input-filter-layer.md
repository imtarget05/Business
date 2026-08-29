# ADR-009: Input Filter Layer

## Status
Accepted

## Context
Raw user input (Telegram, dashboard, API) reaches LLM calls and routing
untreated: control characters, oversize payloads, spam, prompt-injection
attempts and PII all flow into the orchestrator.

## Decision
A dedicated, pure-Python `InputFilter` (packages/core/input_filter.py)
sanitizes input BEFORE any LLM call: normalize → length cap → spam detection →
prompt-injection detection → PII masking → language detection. Injected into
both Orchestrator.execute() and the graph classify node. Blocked inputs return
REJECTED (or raise in graph path) without consuming tokens.

## Consequences
- Zero LLM cost for garbage input; deterministic and testable.
- Filter never mutates non-text payloads; disabled via INPUT_FILTER_ENABLED.
- Injection patterns are a closed list; extend deliberately (see learning loop).
