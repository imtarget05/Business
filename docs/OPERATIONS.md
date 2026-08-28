# Business Ops Agent Swarm — Operations Guide

Multi-agent system: **Supply Chain** (PO parsing → approval → inventory → reporting → n8n export)
+ **Support / Knowledge / Reporting** agents, orchestrated by a supervisor with LLM-based
routing and cross-agent handoff. Plus a **Monitoring agent** (health check, progress report,
research, Telegram push, scheduler) and an **LLM fallback chain** (Ollama → Nous Cloud → Mock).

## Quick start

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env: set LLM_PROVIDER + model. Local default uses Ollama qwen2.5:3b.
pytest tests/            # 420+ tests, all green
```

## LLM providers

| Provider | Config | Notes |
|----------|--------|-------|
| `mock` | `LLM_PROVIDER=mock` | No network, deterministic. Tests/CI use this. |
| `ollama` | `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5:3b` | Local free inference. Ensure model pulled (`ollama pull qwen2.5:3b`). |
| `cloudflare_ai` | `LLM_PROVIDER=cloudflare_ai` + account/token | Nous Cloud / Cloudflare Workers AI. |
| `external_openai_compatible` | base url + key | Any OpenAI-compatible endpoint. |

**Fallback chain (Phase F):** when `LLM_PROVIDER` is a real provider, the factory wraps it in
`FallbackLLMProvider` that auto-switches Ollama → Cloudflare → Mock on timeout/429/unreachable,
so the system never hard-fails. Mock is always the last resort.

> ⚠️ Your `.env` currently has `LLM_MODEL=qwen2.5:3b`. The original scaffold expected `7b`;
> we aligned it to the model actually pulled on this machine. If you pull `7b`, update `.env`.

## Architecture

```
apps/api            FastAPI entrypoint (POST /v1/tasks)
packages/core       Orchestrator, RouterAgent, GraphOrchestrator (LangGraph),
                    registry, policy, tracing (Phase E)
agents/supply_chain PO Agent, Approval, Inventory, Reporting, n8n_client,
                    guardrails + circuit_breaker
agents/support      Support agent (email/gmail tools)
agents/knowledge   Knowledge retrieval agent (pgvector)
agents/reporting   Reporting agent (Google Sheets)
agents/monitoring  health_check, progress_report, research, telegram_bot,
                    scheduler, config loader
```

### Multi-agent flow (Phase C)

1. `Orchestrator` classifies the request (deterministic `domain.action`, or free-text via `RouterAgent`).
2. `registry.get_by_capability` routes to the agent; policy check runs.
3. Agent executes with per-hop timeout. If it returns `handoff` metadata, the orchestrator
   hands off to the target agent (depth-limited, cycle-detected) and merges results.
4. `GraphOrchestrator` (set `LANGGRAPH_ENABLED=true`) runs the same flow as a LangGraph StateGraph.

### Supply chain flow (Phase A + D)

`po_agent → approval → inventory → reporting → n8n_export → END`, each node wrapped in
3-tier guardrails (`*_guardrails.py`) with a `CircuitBreaker` for resilience. n8n export is
non-blocking: if `N8N_WEBHOOK_URL` is unset, it degrades to a no-op.

## Monitoring agent (Phase B)

Run the scheduler (health check every 30 min, daily report at 09:00):

```bash
python -m agents.monitoring.scheduler
```

Telegram push (optional): set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, then send
`/health`, `/report`, `/research <query>`, `/help` to the bot.

Config lives in `config.yaml` (`monitoring:` section) and is overridable by env vars
(`agents/monitoring/config.py` loader merges both).

## Tracing (Phase E)

Set `TRACING_BACKEND=langfuse` (or `otel`) to emit traces. Unset = no-op tracer (zero overhead,
never fails). Langfuse/OTel SDKs are imported lazily; missing SDK → graceful degradation.

## Testing

```bash
pytest tests/            # full suite, ~420 tests
pytest tests/unit/test_llm_fallback.py          # Phase F fallback chain
pytest tests/integration/test_multi_agent_orchestration.py  # Phase C multi-agent
pytest tests/integration/test_supply_chain_n8n.py           # Phase D n8n + circuit breaker
pytest tests/unit/test_tracing.py                        # Phase E tracing
```

## Docker

`docker-compose.yml` brings up the API, Ollama (qwen2.5:3b), Postgres (pgvector), and n8n.
Set `PERSISTENCE_ENABLED=true` in the container for DB-backed task replay.
