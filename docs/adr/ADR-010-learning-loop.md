# ADR-010: Learning Loop

## Status
Accepted

## Context
Routing quality depended on static keyword rules; user corrections were lost.
No mechanism existed to improve routing from feedback over time.

## Decision
1. `POST /v1/feedback` accepts rating (up/down), corrected_capability, comment.
2. `LearningEngine` (packages/core/learning.py) turns corrections into
   `DynamicRule(keyword → capability)` records persisted to
   `data/learned_routing_rules.json` (pilot scale; DB table is the upgrade path).
3. `RouterAgent` applies learned dynamic rules BEFORE static rule fallbacks.
4. `ReflectionEngine` auto-critiques agent outputs (fire-and-forget) to feed
   ratings without human input.
5. A daily scheduler job runs `run_cycle()` and reports totals to Telegram.

## Consequences
- Routing improves without code changes; corrections win over static rules.
- Rules file must be treated as runtime state (volume-mounted in production).
- Feedback endpoint is authenticated by the same API-key middleware as /v1.
