# Business Ops Agent Swarm — Advanced AI Engineering Roadmap & Talking Points

**Phase:** 0+ Skeleton → Production Ready  
**Date:** August 2026  
**Audience:** Engineering / AI / Product Stakeholders  

---

## Executive Summary

The Business Ops Agent Swarm is a multi-agent platform built on FastAPI + LangGraph, with PostgreSQL/pgvector storage, multi-provider LLM abstraction (Ollama/Cloudflare/OpenAI/Mock), and a Telegram-based monitoring bot. The codebase demonstrates strong architectural foundations — registry-driven routing, typed contracts, structured logging with correlation context, and a layered observability stack — but remains in Phase 0+ with key advanced features as optional toggles or planned extensions.

This document presents a technical roadmap across four advanced AI engineering pillars, plus recommendations for end-to-end testing and Telegram UX improvements.

---

## Topic 1: Vector Embeddings & RAG Enhancement

### Current State
- **Knowledge Base** (`packages/core/knowledge_base.py:23`): Uses PostgreSQL full-text search (`tsvector` + GIN index) or in-Python token overlap for SQLite fallback. No vector embeddings.
- **Michelin RAG Cache** (`packages/core/rag_cache.py:12`, `migrations/versions/0010_michelin_rag_cache.py`): Stores facts in a `michelin_facts` table with a `tsv` TSVECTOR column and GIN index. **No embedding vector column.**
- **Embedding Provider** (`packages/llm/mock_embedding.py`): Mock-only embedding provider exists but is not wired into KB/RAG.
- **Provider Abstraction** (`packages/llm/factory.py:18`): `LLMProviderKind` enum supports `mock/cloudflare_ai/external_openai/ollama`. Cloudflare and Ollama can serve embeddings (e.g., `bge-m3`, `nomic-embed-text`).

### Gap Analysis
| Gap | Detail |
|-----|--------|
| No dense retrieval | FTS-only retrieval misses semantic matches (synonyms, paraphrases, Vietnamese text variations) |
| No embedding storage | `michelin_fags` table lacks `VECTOR(768)` column and IVFFlat/HNSW index |
| Unused embedding provider | `MockEmbeddingProvider` in `packages/llm/mock_embedding.py` is not integrated into KB pipeline |
| No reranking | Single-stage retrieval only; no cross-encoder or LLM reranker |

### Recommended Implementation
1. **Add embedding column to `michelin_facts`** — `VECTOR(768)` column + HNSW index in a new migration (extend `migrations/versions/0010_michelin_rag_cache.py`)
2. **Integrate embedding provider** — Wire `packages/llm/factory.py` to produce embeddings via Cloudflare (`@cf/baai/bge-reranker`) or Ollama (`bge-m3`), fallback to mock in tests
3. **Hybrid retrieval** — Combine FTS (exact keyword) + vector (semantic) with weighted scoring
4. **Rerank** — Use LLM provider for cross-encoder reranking of top-k results before synthesis

### Key Files
- `packages/core/knowledge_base.py:23` — `KnowledgeBase` class with `add_document()`, `query()`
- `packages/core/rag_cache.py:12` — `rag_get()`, `rag_store()`, `_query_hash()`
- `packages/llm/factory.py:18` — `get_llm_provider()` maps `LLMProviderKind` → implementation
- `packages/llm/mock_embedding.py` — `MockEmbeddingProvider` class
- `packages/llm/ollama.py:2` — Ollama provider supports `bge-m3` embeddings
- `migrations/versions/0010_michelin_rag_cache.py:8` — `michelin_facts` table definition
- `agents/knowledge/agent.py:34` — Knowledge agent retrieve → synthesize pipeline

---

## Topic 2: LangGraph Orchestration & State Management

### Current State
- **Graph Orchestrator** (`packages/core/graph.py:38`): `GraphOrchestrator` wraps `Orchestrator.execute()` API behind a LangGraph `StateGraph` with `InMemorySaver` checkpointing. `GraphState` TypedDict holds task state.
- **Supply Chain Graph** (`agents/supply_chain/graph.py:15`): Domain-specific `StateGraph` with conditional edges for approval/inventory branching. Also uses `InMemorySaver`.
- **Bootstrap** (`packages/core/bootstrap.py:82`): `build_container()` wires both classic `Orchestrator` and `GraphOrchestrator` side-by-side; `langgraph_enabled` flag toggles usage.
- **Classic Orchestrator** (`packages/core/orchestrator.py:33`): `Orchestrator.execute()` with handoff support, timeout budgets, cycle detection — production-tested via `tests/integration/test_multi_agent_orchestration.py`.

### Gap Analysis
| Gap | Detail |
|-----|--------|
| Checkpoint persistence | `InMemorySaver` is ephemeral — state lost on restart; no Postgres or Redis checkpoint backend |
| No state schema evolution | `GraphState` is a flat TypedDict with no versioned migration strategy |
| Limited graph reuse | Only supply chain agent uses LangGraph; support and knowledge agents use classic orchestrator |
| No subgraph composition | No cross-agent state sharing or supervisor patterns |

### Recommended Implementation
1. **Persistent checkpointing** — Replace `InMemorySaver` with `PostgresCheckpoint` from `langgraph-checkpoint-postgres` for state durability
2. **Unified graph architecture** — Migrate all agents (support, knowledge, reporting) to LangGraph state graphs for consistency
3. **Supervisor patterns** — Implement hierarchical multi-agent workflows where a supervisor `StateGraph` routes sub-tasks to child agent graphs
4. **State schema evolution** — Version `GraphState` and implement migration hooks for backward compatibility

### Key Files
- `packages/core/graph.py:38` — `GraphOrchestrator`, `GraphState` TypedDict, `InMemorySaver`
- `packages/core/graph.py:62` — `create_graph()` builds the `StateGraph` with node definitions
- `agents/supply_chain/graph.py:15` — `build_supply_chain_graph()` with conditional edges
- `packages/core/bootstrap.py:82` — `build_container()` composition root
- `packages/core/orchestrator.py:33` — `Orchestrator` classic execution with handoff logic
- `tests/integration/test_langgraph_orchestrator.py:22` — Graph integration tests (C1, C3, C4-C6)
- `packages/config/settings.py:112` — `langgraph_enabled` flag

---

## Topic 3: LLM Cost Optimization & Intelligence

### Current State
- **Cost Tracking** (`packages/core/llm_cost.py:23`): Token estimation (~4 chars/token), JSONL ledger (`llm_usage.jsonl`), on-disk prompt cache (`data/llm_cache`, 3600s TTL). `estimate_tokens()`, `log_llm_usage()`, `prompt_cache_key()`/`prompt_cache_get/set()`.
- **Pricing Table** (`packages/core/llm_cost.py:27`): `qwen3:1.7b` and `qwen2.5` priced at $0.00 (self-hosted Ollama = free); external models priced per-token.
- **Fallback Chain** (`packages/llm/fallback.py:11`): `FallbackLLMProvider` chain: Ollama → Cloudflare → Mock; sticky active provider + 30s cooldown (`_cooldown_until` field).
- **Cost Reporting** (`scripts/report_llm_cost.py:5`): Reads ledger, prints cache-hit rate, per-model breakdown, top tags.
- **MLOps Reporting** (`scripts/mlops_report.py:3`): Reads `agent_runs` + `task_feedback` tables, prints success rate + 👍/👎 ratio.

### Gap Analysis
| Gap | Detail |
|-----|--------|
| Cache key is brittle | Uses `hashlib.md5` of full prompt string — no normalization; minor whitespace changes cause misses |
| No budget enforcement | Tracking exists but no runtime guardrails to reject/stop expensive calls |
| Fallback is time-based only | 30s cooldown may be too long or too short; no quality-based routing |
| No cost attribution | No per-agent, per-task, or per-user cost breakdown in reporting |

### Recommended Implementation
1. **Smart prompt caching** — Normalize prompts (strip whitespace, canonical JSON) before hashing; add cache warming for common queries
2. **Cost budget guardrails** — Add `LLMCostBudget` middleware that tracks per-task spend and escalates/stops when threshold exceeded
3. **Quality-aware fallback** — Route simple queries to cheaper/smaller models (e.g., `qwen2.5:3b`); complex queries to `qwen3:1.7b` or Cloudflare; use confidence scores to trigger fallback
4. **Enhanced reporting** — Add per-agent, per-capability, per-user cost attribution to `report_llm_cost.py`

### Key Files
- `packages/core/llm_cost.py:23` — `LLM_COSTS` pricing table, `estimate_tokens()`, `log_llm_usage()`
- `packages/core/llm_cost.py:52` — `prompt_cache_key()` with `hashlib.md5`
- `packages/core/llm_cost.py:68` — `prompt_cache_get/set()` with TTL
- `packages/llm/fallback.py:11` — `FallbackLLMProvider` with `_active_provider`, `_cooldown_until`
- `scripts/report_llm_cost.py:5` — Cost report generator
- `scripts/mlops_report.py:3` — MLOps metrics reporter
- `tests/unit/test_llm_cost.py:31` — Tests for token estimation, logging, caching
- `tests/unit/test_llm_fallback.py:15` — Tests for fallback provider switching

---

## Topic 4: Observability & Monitoring

### Current State
- **Metrics** (`packages/observability/metrics.py:18`): `MetricsRegistry` with counters, timers, `snapshot()`. Uses ContextVar `boas_metrics` for context-aware tracking.
- **Logging** (`packages/observability/logging.py:1`, `packages/observability/context.py:1`): Structured JSON logging with `request_id`, `trace_id`, `task_id` correlation context.
- **Tracing** (`packages/core/tracing.py:12`): `Tracer` ABC; `NoOp` (default), `Langfuse`, `OTel` backends via `TRACING_BACKEND` env var.
- **Audit Layer** (`packages/core/audit.py:1`): `AuditService`, `AuditEvent`, `classify_risk()` — records security/sensitive operations.
- **Health Check** (`agents/monitoring/health_check.py:15`): `run_health_check()` with `ComponentCheck`/`HealthCheckResult`; checks API, DB, agent registry.
- **Telegram Monitoring** (`agents/monitoring/telegram_bot.py:8`): Bot reports `/health`, `/report`, `/research`, `/help`.
- **Scheduler** (`agents/monitoring/scheduler.py:10`): APScheduler — 30-min health checks, daily 09:00 AM report (Asia/Ho_Chi_Minh timezone).

### Gap Analysis
| Gap | Detail |
|-----|--------|
| No dashboard | Metrics infrastructure exists but no Grafana/dashboard for business metric visualization |
| Limited alerting | Health checks run but no threshold-based alerting (e.g., success rate < 95%) |
| No trace sampling | Langfuse/OTel backends available but no sampling policy for cost control |
| Audit is passive | Risk events logged but no automated response or alert escalation |

### Recommended Implementation
1. **Grafana dashboard** — Deploy with Prometheus data source; dashboards for agent success rate, LLM cost/usage, KB hit rate, Telegram bot response latency
2. **Threshold-based alerting** — Configure alerts for: success rate < 95%, error rate > 5%, LLM cost > $50/day, DB connection pool > 80%
3. **Trace sampling** — Adaptive sampling: 100% for errors, 10% for success, tag with business task type for filtering
4. **Active audit response** — Risk level `HIGH` triggers immediate Slack/Telegram alert; `MEDIUM` events aggregated into daily report

### Key Files
- `packages/observability/metrics.py:18` — `MetricsRegistry`, `Counter`, `Timer`, `snapshot()`
- `packages/observability/logging.py:1` — JSON structured logging setup
- `packages/observability/context.py:1` — `CorrelationContext` with `request_id`/`trace_id`/`task_id`
- `packages/core/tracing.py:12` — `Tracer` ABC + `NoOpTracer`/`LangfuseTracer`/`OtelTracer`
- `packages/core/audit.py:1` — `AuditService`, `AuditEvent`, `classify_risk()`
- `agents/monitoring/health_check.py:15` — `run_health_check()`, `ComponentCheck`, `HealthCheckResult`
- `agents/monitoring/scheduler.py:10` — `MonitoringScheduler` (30-min checks, daily reports)
- `tests/unit/test_health_check.py:12` — Tests for health check components

---

## E2E Testing Strategy

### Current State
- **Integration tests** (`tests/integration/`): LangGraph orchestrator (C1, C3, C4-C6), multi-agent handoff (support→knowledge). Test both classic and graph paths.
- **Unit tests** (`tests/unit/`): Mock LLM provider for deterministic testing; covers fallback, cost tracking, RAG cache, knowledge base, supply chain pipeline, health check, monitoring config, Telegram bot, handoff chains.
- **Gap: No true end-to-end test** spanning Telegram message → API endpoint → orchestrator → LLM → KB/RAG → Telegram bot reply.

### Recommended E2E Tests

#### Test 1: Full Telegram Support Flow
```
Scenario: User sends Vietnamese support query via Telegram
1. Telegram bot receives /research command
2. Bot calls POST /v1/tasks with TaskRequest(domain=SUPPORT, action="triage")
3. Router classifies intent → routes to support-v1 agent
4. Support agent calls knowledge.query (handoff) → knowledge-v1 agent
5. Knowledge agent queries KB (FTS + vector) → synthesizes answer
6. LLM generates response (fallback chain: Ollama → Cloudflare → Mock)
7. Response traced via Tracer, logged via JSON logger, timed via MetricsRegistry
8. Telegram bot sends formatted reply with citations
9. Feedback endpoint records thumbs up/down
```

#### Test 2: Michelin RAG Cache Warm + Hit
```
Scenario: User queries food recommendation in Vietnamese
1. First query → Miss → LLM generates → Response cached in michelin_facts + prompt cache
2. Second query (same or paraphrased) → Cache hit → Response served from cache
3. Cost tracked: cache hit = $0.00, cache miss = $X
4. Success rate = 100%, latency < 500ms
```

#### Test 3: Supply Chain Approval Workflow
```
Scenario: Inbound order requires manual approval
1. Task received → Supply chain graph node: validate inbound
2. Conditional edge: PO amount < $10K → auto-approve; > $10K → manual approval
3. Approval branch: notify via Telegram bot with inline keyboard (Approve/Reject)
4. User presses Approve → state transitions → inventory update node → reporting node
5. Final state checkpointed in Postgres
6. All transitions recorded in agent_runs table for MLOps reporting
```

### Test Infrastructure
- Use `tests/conftest.py` fixtures (`classic_container`, `graph_container`) for consistent setup
- Extend with `telegram_client` fixture (mock `aiogram`-style bot) for E2E test harnesses
- Mock LLM provider in `tests/unit/test_llm_cost.py` pattern for deterministic, fast E2E runs
- Add `pytest.mark.e2e` marker for CI pipeline separation (run E2E nightly, integration/tests on PR)

---

## Telegram UX Improvements

### Current State
- **Telegram Bot** (`agents/monitoring/telegram_bot.py:8`): Supports `/health`, `/report`, `/research`, `/help` commands. Vietnamese-specific logic for food/Michelin queries (`_summarize_food`, `_needs_web_lookup`). Inline keyboards for navigation.
- **Config** (`agents/monitoring/config.py:23`): `MonitoringConfig`, `TelegramConfig`, `SchedulerConfig`; loaded from YAML + env via `load_monitoring_config()`.
- **Vietnamese support**: Web-lookup regex for Vietnamese food queries, markdown escaping for reply formatting.

### Gap Analysis
| Gap | Detail |
|-----|--------|
| No conversation context | Each Telegram message is treated independently; no session/state tracking across messages |
| Limited interactive elements | Basic inline keyboards only; no reply markup, no callback queries for multi-step flows |
| No typing indicators | Bot doesn't show "typing..." during LLM calls (poor perceived performance) |
| No Vietnamese NLP | Intent classification uses simple keyword matching; no proper tokenizer/lemmatizer for Vietnamese |

### Recommended Improvements (cải thiện trải nghiệm người dùng trên telegram)

#### 1. Conversation Context Tracking
- Store per-user `TaskContext` (Vietnamese: "ngữ cảnh người dùng") in Redis or in-memory session store
- Support multi-turn conversations: bot remembers previous question, can ask follow-ups
- Implement session timeout (e.g., 30 minutes of inactivity)

#### 2. Rich Interactive Elements
- Add **reply keyboards** with quick-reply buttons for common queries (Vietnamese: "truy vấn thực địa", "tìm món ăn", "báo cáo hệ thống")
- Use **callback queries** for approval workflows (e.g., supply chain approval via Telegram: `[Phê duyệt]`, `[Từ chối]`)
- Add **pagination** for long results (e.g., multi-item food recommendations with `< Trước` / `Tiếp >` buttons)

#### 3. Typing Indicators & Progress Feedback
- Send `send_chat_action("typing")` before LLM calls (Vietnamese: "đang gõ tin nhắn...")
- For long-running operations, send progress messages: "🔍 Đang tìm thông tin..." → "🤔 Đang phân tích..." → "✍️ Đang soạn câu trả lời..."

#### 4. Vietnamese Language Enhancements
- Integrate a Vietnamese tokenizer (e.g., `underthesea` or `vncorenlp`) for better intent classification
- Add Vietnamese-specific quick replies based on detected entities (restaurant names, food types, locations)
- Support Vietnamese input variants: "tìm quán ăn", "gợi ý món ngon", "đánh giá nhà hàng" → normalize to `knowledge.query` capability

#### 5. Rich Media & Formatting
- Render food recommendations with **inline images** (Telegram photo messages with caption)
- Use **markdownV2** formatting for food attributes: **tên quán**, *địa chỉ*, `giá`, **thời gian mở cửa**
- Add **location sharing** support: bot can request user location and return nearby food recommendations

### Key Files
- `agents/monitoring/telegram_bot.py:8` — Main Telegram bot handler
- `agents/monitoring/telegram_bot.py:34` — `_summarize_food()` for Vietnamese food queries
- `agents/monitoring/telegram_bot.py:25` — `_needs_web_lookup()` with food/Michelin regex
- `agents/monitoring/config.py:23` — `MonitoringConfig`, `TelegramConfig`
- `agents/monitoring/scheduler.py:10` — `MonitoringScheduler` with daily reports
- `agents/monitoring/health_check.py:15` — Health check logic sent to Telegram
- `tests/unit/test_telegram_bot.py:12` — Tests for `/health`, `/report`, `/research`, `/help` commands

---

## Implementation Priority

| Priority | Topic | Timeline | Effort |
|----------|-------|----------|--------|
| **P0** | LangGraph persistent checkpointing | 2-3 days | Small |
| **P0** | Vector embeddings in RAG | 3-4 days | Medium |
| **P1** | E2E test suite | 2-3 days | Medium |
| **P1** | Telegram UX improvements | 4-5 days | Medium |
| **P2** | Grafana dashboards + alerting | 3-4 days | Small |
| **P2** | LLM cost budget guardrails | 2-3 days | Small |
| **P3** | Full graph migration (all agents) | 1-2 weeks | Large |
| **P3** | Active audit response | 2-3 days | Small |

---

## Key Metrics to Track

| Metric | Current | Target | Owner |
|--------|---------|--------|-------|
| Agent success rate | ~90% (estimated) | >95% | Orchestrator |
| KB hit rate | 0% (no vector) | >60% | Knowledge Agent |
| LLM cache hit rate | ~20% (FTS-only) | >40% | `llm_cost.py` |
| Telegram response time | N/A (unmeasured) | <2s avg | Telegram Bot |
| Handoff success rate | Tested via mocks | >90% (real LLM) | Orchestrator |
| Daily active Telegram users | N/A | Track growth | Monitoring |