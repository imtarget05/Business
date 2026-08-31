# Business Ops Agent Swarm — Interview Preparation

## 1. System Overview

Business Ops Agent Swarm is a multi-agent AI platform for business operations (supply chain, support, knowledge, reporting) built with **FastAPI + LangGraph + Neon PostgreSQL (pgvector)**. It orchestrates 15+ domain agents behind a unified `LLMProvider` abstraction with a fallback chain (Ollama ? Cloudflare AI ? Mock), hybrid retrieval (FTS + vector with RRF fusion), multi-tier caching, and a defense-in-depth hallucination prevention strategy. The system serves both a Next.js dashboard and external integrations (n8n, Telegram) through a single API gateway.

---

## 2. Architecture Diagram

```
+-----------------------------------------------------------------------------+
¦                              CLIENTS                                        ¦
¦  +--------------+  +--------------+  +--------------+  +--------------+   ¦
¦  ¦ Next.js       ¦  ¦ n8n          ¦  ¦ Telegram     ¦  ¦ External     ¦   ¦
¦  ¦ Dashboard     ¦  ¦ Webhooks     ¦  ¦ Bot          ¦  ¦ API Consumers¦   ¦
¦  +--------------+  +--------------+  +--------------+  +--------------+   ¦
+---------+-----------------+-----------------+-----------------+------------+
          ¦                 ¦                 ¦                 ¦
          ?                 ?                 ?                 ?
+-----------------------------------------------------------------------------+
¦                          FASTAPI GATEWAY (apps/api)                         ¦
¦  +---------------------------------------------------------------------+    ¦
¦  ¦  Middleware Pipeline (order matters):                               ¦    ¦
¦  ¦  1. Request Context (trace_id, request_id injection)                ¦    ¦
¦  ¦  2. Auth (DB-backed API keys ? organization_id)                     ¦    ¦
¦  ¦  3. Rate Limiting (sliding window per API key)                      ¦    ¦
¦  ¦  4. Input Filter (sanitize ? length cap ? spam ? injection ? PII)   ¦    ¦
¦  +---------------------------------------------------------------------+    ¦
¦  +---------------------------------------------------------------------+    ¦
¦  ¦  Routes: /v1/tasks · /v1/agents · /v1/feedback · /v1/conversations ¦    ¦
¦  ¦          /health · /ready · /metrics · /v1/knowledge               ¦    ¦
¦  +---------------------------------------------------------------------+    ¦
+-----------------------------------------------------------------------------+
                                  ¦
                                  ?
+-----------------------------------------------------------------------------+
¦                          ORCHESTRATOR (packages/core)                        ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  ¦  Orchestrator / GraphOrchestrator (LangGraph StateGraph)              ¦  ¦
¦  ¦                                                                       ¦  ¦
¦  ¦  State Machine: PENDING ? CLASSIFYING ? ROUTING ? RUNNING ?          ¦  ¦
¦  ¦                  VALIDATING ? COMPLETED / FAILED / DEAD_LETTERED      ¦  ¦
¦  ¦                                                                       ¦  ¦
¦  ¦  +------------+  +------------+  +------------+  +------------+    ¦  ¦
¦  ¦  ¦ Classify   ¦? ¦ Route      ¦? ¦ Execute    ¦? ¦ Validate   ¦    ¦  ¦
¦  ¦  ¦ (LLM +     ¦  ¦ (Registry  ¦  ¦ (per-hop   ¦  ¦ (citations ¦    ¦  ¦
¦  ¦  ¦  keyword)  ¦  ¦  lookup)   ¦  ¦  timeout)  ¦  ¦  required) ¦    ¦  ¦
¦  ¦  +------------+  +------------+  +------------+  +------------+    ¦  ¦
¦  ¦                                                                       ¦  ¦
¦  ¦  Handoff chain: depth-limited, cycle-detected, policy-gated          ¦  ¦
¦  ¦  Retry: 1 automatic retry on transient errors ? dead-letter          ¦  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  ¦  RouterAgent: intent classification (LLM structured + keyword fallback¦  ¦
¦  ¦  Registry: capability-based agent lookup ("domain.action")            ¦  ¦
¦  ¦  Policy: per-capability authorization (READ/WRITE/DESTRUCTIVE)        ¦  ¦
¦  +-----------------------------------------------------------------------+  ¦
+-----------------------------------------------------------------------------+
                                  ¦
                                  ?
+-----------------------------------------------------------------------------+
¦                          DOMAIN AGENTS (agents/*)                            ¦
¦  +----------+ +----------+ +----------+ +----------+ +----------+          ¦
¦  ¦Knowledge ¦ ¦Support   ¦ ¦Reporting ¦ ¦Research  ¦ ¦Supply    ¦          ¦
¦  ¦(RAG+     ¦ ¦(triage/  ¦ ¦(Sheets)  ¦ ¦(web/     ¦ ¦Chain     ¦          ¦
¦  ¦ hybrid)  ¦ ¦ draft)   ¦ ¦          ¦ ¦ arxiv)   ¦ ¦(PO/inv)  ¦          ¦
¦  +----------+ +----------+ +----------+ +----------+ +----------+          ¦
¦  +----------+ +----------+ +----------+ +----------+ +----------+          ¦
¦  ¦Gmail     ¦ ¦Calendar  ¦ ¦Context   ¦ ¦YouTube   ¦ ¦Root Cause¦          ¦
¦  ¦          ¦ ¦          ¦ ¦(memory)  ¦ ¦          ¦ ¦          ¦          ¦
¦  +----------+ +----------+ +----------+ +----------+ +----------+          ¦
+-----------------------------------------------------------------------------+
                                  ¦
                                  ?
+-----------------------------------------------------------------------------+
¦                          INFRASTRUCTURE LAYER                               ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  ¦  LLM Provider Abstraction (packages/llm)                             ¦  ¦
¦  ¦  +---------+   +--------------+   +-------------+   +------------+  ¦  ¦
¦  ¦  ¦ Ollama  ¦ ? ¦ Cloudflare AI¦ ? ¦ OpenAI-compat¦ ? ¦ Mock       ¦  ¦  ¦
¦  ¦  ¦ (local) ¦   ¦ (Nous Cloud) ¦   ¦ (external)  ¦   ¦ (always)   ¦  ¦  ¦
¦  ¦  +---------+   +--------------+   +-------------+   +------------+  ¦  ¦
¦  ¦  Fallback chain: sticky-active, cooldown-based, never hard-fails      ¦  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  ¦  Database: Neon PostgreSQL + pgvector                                 ¦  ¦
¦  ¦  +--------------+  +--------------+  +--------------+                ¦  ¦
¦  ¦  ¦ Relational   ¦  ¦ Vector       ¦  ¦ RAG Cache    ¦                ¦  ¦
¦  ¦  ¦ (tasks,users,¦  ¦ (embeddings, ¦  ¦ (verified    ¦                ¦  ¦
¦  ¦  ¦  audit_logs) ¦  ¦  1536-dim)   ¦  ¦  answers)    ¦                ¦  ¦
¦  ¦  +--------------+  +--------------+  +--------------+                ¦  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  +-----------------------------------------------------------------------+  ¦
¦  ¦  Observability: Structured JSON logs · Prometheus · Langfuse/OTel    ¦  ¦
¦  +-----------------------------------------------------------------------+  ¦
+-----------------------------------------------------------------------------+

---

## 3. Key Technical Decisions (with Trade-offs)

### Why LangGraph over raw LangChain?
- **Decision**: Use LangGraph `StateGraph` for the orchestrator flow; classic `Orchestrator` remains default.
- **Trade-off**: LangGraph gives us checkpointing (PostgresSaver), explicit state machine visualization, and conditional edges for retry/handoff/dead-letter routing. Raw LangChain agents would require us to build our own state persistence and cycle detection.
- **Cost**: Additional dependency, but the graph abstraction maps directly to our `PENDING ? CLASSIFYING ? ROUTING ? RUNNING ? VALIDATING ? terminal` lifecycle.

### Why hybrid retrieval (FTS + vector)?
- **Decision**: Combine PostgreSQL full-text search (`tsvector`) with pgvector cosine similarity, fused via Reciprocal Rank Fusion (RRF).
- **Trade-off**: FTS excels at exact keyword matches (Vietnamese-safe with `simple` config); vector captures semantic similarity. RRF (k=60) avoids brittle score normalization across heterogeneous scales. Pure FTS misses paraphrases; pure vector misses rare terms.
- **Result**: Recall@5 = 1.000 (all relevant docs retrieved within top-5).

### Why fallback chain with Mock at the end?
- **Decision**: `FallbackLLMProvider` wraps Ollama ? Cloudflare AI ? OpenAI-compatible ? Mock.
- **Trade-off**: Mock is always the last resort so the system **never hard-fails** — critical for production availability. Trade-off: mock responses are degraded (deterministic, no real reasoning), but the API contract stays satisfied and the user gets a graceful response instead of a 500.
- **Mechanism**: sticky-active provider, 30s cooldown to prevent flapping, failover on `TimeoutError`/`ConnectionError`/429.

### Why pgvector over dedicated vector DB?
- **Decision**: Use Neon PostgreSQL with pgvector extension.
- **Trade-off**: One engine for relational + vector simplifies ops on Neon free tier. Dedicated vector DBs (Pinecone/Qdrant) add cost and operational complexity we don't need at current scale (17 docs ? hundreds). Revisit at retrieval-quality stage if we outgrow pgvector's IVFFlat/HNSW indexing.

---

## 4. Hallucination Prevention Strategy

1. **Hard "no answer without context" rule**: KnowledgeAgent returns `"no relevant information found"` when hybrid retrieval yields zero chunks — LLM is never called on empty context (`agents/knowledge/agent.py:169-178`).
2. **Hybrid retrieval with similarity floor**: Vector chunks must pass `min_similarity=0.5` cosine threshold *before* RRF fusion. Prevents weak semantic matches from reaching the LLM.
3. **Mandatory citations**: `requires_citations` metadata flag forces knowledge agents to include `Citation` objects (source_id, title, snippet). Orchestrator validation rejects success responses without citations (`orchestrator.py:206-207`).
4. **Structured output with confidence**: LLM responds via `_AnswerOut` Pydantic schema with `confidence: float` (0.0–1.0). Low-confidence answers are flagged in metadata for downstream review.
5. **Context-only system prompt**: LLM instruction: *"Answer ONLY from the provided context. Cite supporting blocks as [n]. If context does not contain the answer, say so."*

---

## 5. Cost Optimization

| Strategy | Implementation | Impact |
|----------|---------------|--------|
| **RAG cache** | `rag_cache.py`: exact hash ? FTS ? vector lookup for repeat questions | Cache hit = $0 (no LLM, no embedding call) |
| **Prompt cache** | `llm_cost.py`: SHA-256 keyed on-disk cache with TTL (default 1h) | Identical prompts served from disk |
| **Token estimation** | `estimate_tokens()`: ~4 chars/token conservative estimate | Trend detection, not invoicing |
| **Self-hosted Ollama** | Local qwen2.5:3b inference | $0 for input + output tokens |
| **Input filter short-circuit** | Block spam/injection *before* any LLM call | Zero token spend on garbage |
| **Retrieval short-circuit** | Skip vector pass when FTS returns =top_k results | Saves embedding API calls |
| **Usage ledger** | `llm_usage.jsonl` with model/tag/latency/cost | Audit trail for spend analysis |

**Key metric**: Cache hit = $0 spend. Prometheus `boas_rag_cache_hits_total` tracks hit rate.

---

## 6. Performance / Latency

- **Retrieval short-circuit**: If FTS returns =top_k candidates, vector retrieval is skipped entirely (`agents/knowledge/agent.py:117-119`).
- **Fallback chain with cooldown**: 30s cooldown prevents flapping between providers on transient errors. Sticky-active: once a provider succeeds, it stays active.
- **Timeouts at every boundary**:
  - Per-hop agent timeout: `agent_hop_timeout_seconds` (default 30s)
  - Total chain cap: 2× `agent_task_timeout_seconds` (default 60s)
  - Embedding call: 30s thread-pool timeout
- **Retry with exponential backoff**: 1 automatic retry on transient errors (timeout, `ToolExecutionError`) ? dead-letter after exhaustion.
- **Async-first**: All I/O (LLM, DB, embeddings) is `asyncio`-native. `asyncio.wait_for` enforces deadlines.

---

## 7. Security

| Layer | Implementation |
|-------|---------------|
| **Input sanitization** | `input_filter.py`: normalize ? strip control chars ? length cap ? spam detection ? prompt-injection regex ? PII masking (email/VN phone/CCCD/card) ? language detection |
| **DB-backed API keys** | `ApiKeyRepository.verify()` against `api_keys` table; HMAC comparison for metrics token |
| **Rate limiting** | Sliding window per API key (default 60 req/min); returns `429` with `X-RateLimit-*` headers |
| **Fail-closed startup** | Refuses to start in non-local env without `api_key` or `tenant_api_keys` configured |
| **Error sanitization** | Stack traces never exposed to clients; `RequestValidationError` strips non-serializable `ctx` |
| **PII masking** | Emails ? `a***@gmail.com`, phones ? `0912***678`, IDs ? partial masking |
| **Prompt injection** | 5 regex patterns: instruction override, role override, meta-prompt probe, prompt leak, special token injection |

---

## 8. Error Handling

- **Standardized error model**: `BusinessOpsError` base class with `ErrorCode` StrEnum (16 codes), stable JSON payload `{"error": {"code": "...", "message": "...", "task_id": "..."}}`, HTTP status mapping (`errors.py`).
- **Graceful degradation**: 
  - n8n export degrades to no-op if webhook URL unset
  - Tracing falls back to `NoOpTracer` if SDK missing
  - Embedding failures return `None` (trust retriever)
  - RAG cache failures return `None` (proceed to LLM)
- **Dead-letter pattern**: After retry exhaustion, task transitions to `DEAD_LETTERED` state with full error context preserved (`orchestrator.py:660-690`).
- **Retry policy**: Exactly 1 automatic retry on transient errors (`AgentTimeoutError`, `ToolExecutionError`). Handoff errors (depth exceeded, cycle detected) never retry.
- **Circuit breaker**: `agents/supply_chain/circuit_breaker.py` — CLOSED ? OPEN (5 failures) ? HALF_OPEN (probe) ? CLOSED. Thread-safe via `asyncio.Lock`.

---

## 9. Scalability Considerations

| Aspect | Current State | Future Improvement |
|--------|--------------|-------------------|
| **Architecture** | Async-first (`asyncio`-native throughout) | — |
| **API layer** | Stateless FastAPI (org_id from API key, not session) | Horizontal scaling behind load balancer |
| **State** | In-memory `InMemoryAgentRegistry`, in-memory rate limiter | Redis for shared state; DB-backed registry sync |
| **Checkpoints** | `InMemorySaver` (default) or `PostgresSaver` (configured) | PostgresSaver for multi-worker checkpoint sharing |
| **Task queue** | Direct `asyncio` execution | Message queue (Celery/ARQ) for backpressure |
| **LLM calls** | Single-process async | Batch inference, request coalescing |
| **Database** | Neon serverless Postgres (auto-scaling) | Read replicas for query load |

**Current limitation**: `InMemoryAgentRegistry` is per-process; multi-worker production needs DB-backed descriptor sync.

---

## 10. Monitoring / Observability

- **Structured JSON logging** (`observability/logging.py`): `JsonFormatter` auto-injects `request_id`, `trace_id`, `task_id`, `agent_run_id` from context. All logs are single-line JSON for ELK/Loki ingestion.
- **Prometheus metrics** (`observability/metrics.py`):
  - `boas_agent_success_total` (agent, domain, status)
  - `boas_llm_cost_usd_total` (model, tag)
  - `boas_rag_cache_hits_total` / `boas_rag_cache_misses_total`
  - `boas_handoff_total` (from_agent, to_agent)
  - Exposed at `GET /metrics` (token-gated via `METRICS_TOKEN`)
- **Pluggable tracing** (`core/tracing.py`): `NoOpTracer` (default, zero overhead) ? `LangfuseTracer` or `OTelTracer` via `TRACING_BACKEND` env. Lazy SDK import; missing SDK ? graceful degradation.
- **Grafana dashboards**: Pre-provisioned in `monitoring/grafana/dashboards/`.
- **Alerting**: Prometheus `prometheus.yml` with scrape configs; alert rules wired to Grafana.

---

## 11. Common Interview Questions & Answers

### "How does your RAG system work?"
> Hybrid retrieval: FTS (`tsvector`, `simple` config for Vietnamese) runs first; if results < top_k, vector retrieval (pgvector cosine) runs. Results fuse via Reciprocal Rank Fusion (k=60). Chunks pass `min_similarity=0.5` floor *before* fusion. LLM synthesizes answer with mandatory citations. If zero chunks: return "no relevant information found" — LLM never called without context.

### "How do you prevent hallucination?"
> Four-layer defense: (1) hard rule — no LLM call without retrieved context; (2) similarity floor filters weak vector matches; (3) mandatory citations on knowledge responses (validated by orchestrator); (4) structured output with confidence score. System prompt enforces "answer ONLY from provided context."

### "How do you handle LLM failures?"
> Fallback chain: Ollama (local) ? Cloudflare AI ? OpenAI-compatible ? Mock. Sticky-active with 30s cooldown. Mock is always last resort — system never hard-fails. At orchestrator level: 1 retry on transient errors ? dead-letter with full context. Circuit breaker in supply chain domain prevents cascading failures.

### "How do you control costs?"
> Multi-tier caching: RAG cache (exact hash ? FTS ? vector) and prompt cache (SHA-256 keyed, TTL-based). Cache hit = $0. Self-hosted Ollama = free inference. Input filter blocks spam/injection before any token spend. Retrieval short-circuit skips expensive vector pass when FTS suffices. Usage ledger (`llm_usage.jsonl`) tracks every call with model, tokens, estimated cost.

### "How do you secure AI agents?"
> Defense in depth: input filter (prompt injection regex, PII masking, length cap), DB-backed API keys with HMAC verification, sliding-window rate limiting, fail-closed startup (refuses to boot without auth), error sanitization (no stack traces), and per-capability policy checks (READ/WRITE/DESTRUCTIVE risk levels).

### "How do you monitor agents in production?"
> Three pillars: structured JSON logs with correlation IDs (trace_id/task_id/agent_run_id), Prometheus business counters (agent results, LLM cost, cache hits, handoffs) scraped to Grafana, and pluggable tracing (Langfuse/OTel) with zero-overhead NoOp default. All telemetry is fire-and-forget — never breaks the pipeline.

### "What would you scale first?"
> Replace in-memory rate limiter with Redis (current limiter is per-process, breaks under multi-worker). Then add a message queue (ARQ/Celery) for task backpressure. Then DB-backed agent registry for multi-worker consistency. These unblock horizontal scaling of the API layer.

### "How do you evaluate RAG quality?"
> Golden dataset: 12 evaluation queries against 17 indexed documents. Metrics: Precision@1/3/5, Recall@5, MRR for FTS, vector, and hybrid methods. Current results: Recall@5 = 1.000 (hybrid), MRR = 0.746. Evaluation runs via `scripts/run_rag_evaluation.py`.

---

## 12. Metrics & Results

### Retrieval Performance (from `docs/metrics.md`)

| Method | P@1 | P@3 | P@5 | Recall@5 | MRR |
|--------|-----|-----|-----|----------|-----|
| FTS      | 0.583 | 0.306 | 0.204 | 1.000 | 0.771 |
| VECTOR   | 0.667 | 0.250 | 0.167 | 0.833 | 0.757 |
| HYBRID   | 0.583 | 0.278 | 0.200 | 1.000 | 0.746 |

- **Dataset**: 12 queries, 17 documents (Vietnamese geography, AI/ML, business ops, technology, agent architecture)
- **Key finding**: All methods perform similarly; hybrid achieves best Recall@5 (1.000) — every relevant document retrieved within top-5.

### System Metrics (Prometheus)
- `boas_agent_success_total` — agent execution outcomes
- `boas_llm_cost_usd_total` — estimated spend by model
- `boas_rag_cache_hits_total` / `boas_rag_cache_misses_total` — cache effectiveness
- `boas_handoff_total` — multi-agent delegation frequency

---

## 13. Known Limitations & Future Work

| Limitation | Impact | Future Improvement |
|-----------|--------|-------------------|
| **In-memory rate limiter** | Breaks under multi-worker deployment | Redis-backed distributed limiter |
| **No circuit breaker (orchestrator-level)** | Transient failures cascade within a request | Extend existing supply chain `CircuitBreaker` to orchestrator |
| **No distributed tracing propagation** | Cross-service traces break at API boundary | W3C Trace Context propagation, OTel baggage |
| **No SLO/SLI tracking** | Can't measure reliability targets | Define SLIs (latency p99, error rate) ? SLOs ? error budgets |
| **No ML-based prompt injection detection** | Regex patterns have false positives/negatives | Fine-tuned classifier (e.g., DeBERTa) for injection detection |
| **In-memory agent registry** | Multi-worker needs shared state | DB-backed descriptor sync |
| **No task queue** | No backpressure under load | ARQ/Celery for async task execution |
| **Fixed embedding dimension (1536)** | Provider change requires migration | Abstract dimension in embedding provider |
| **No multi-tenant isolation in vector search** | All orgs share same KB | Row-level security or per-org collections |
| **No A/B testing framework** | Can't compare prompt/model variants | Experiment assignment in orchestrator |

---

## Quick Reference: Key Files

| Component | File |
|-----------|------|
| Orchestrator | `packages/core/orchestrator.py` |
| LangGraph flow | `packages/core/graph.py` |
| Hybrid retrieval | `packages/core/hybrid_retrieval.py` |
| RAG cache | `packages/core/rag_cache.py` |
| LLM fallback | `packages/llm/fallback.py` |
| LLM cost tracking | `packages/core/llm_cost.py` |
| Input filter | `packages/core/input_filter.py` |
| Error model | `packages/core/errors.py` |
| Knowledge agent | `agents/knowledge/agent.py` |
| Circuit breaker | `agents/supply_chain/circuit_breaker.py` |
| Metrics | `packages/observability/metrics.py` |
| Logging | `packages/observability/logging.py` |
| Tracing | `packages/core/tracing.py` |
| API gateway | `apps/api/main.py` |
| ADR records | `docs/adr/ADR-*.md` |
