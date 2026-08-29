# ADR-012: Capability-Based Routing

## Status
Accepted

## Context
Phase 0 routing resolved a caller-supplied capability string via the registry.
Free-text intent classification only produced one exact intent (accept/reject),
with no notion of ranking multiple candidate agents.

## Decision
- `RouterAgent.candidates(text)` scores every registered agent by keyword/capability
  overlap, returning ranked `(qualified_name, score)` — enabling the Planner to
  assemble a minimal multi-agent chain instead of broadcasting.
- LLM structured classification still runs; its result is only accepted if it
  lands in the routing table and clears the confidence threshold, otherwise the
  LLM path falls through to rule-based + dynamic-rule + escalation.
- Learned dynamic rules (ADR-010) are consulted before static `RULE_FALLBACKS`.

## Consequences
- Routing becomes "infer intent -> score candidates -> policy check -> select",
  never broadcast to all 60.
- Candidates are deterministic and zero-LLM-cost (keyword overlap).
