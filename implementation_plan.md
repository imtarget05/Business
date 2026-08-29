# Implementation Plan — Production 24/7 + Input Filter Layer + Learning Loop

[Overview]
Đưa Business Ops Agent Swarm lên production chạy 24/7: toàn bộ agent (9 nhóm) được host
và kích hoạt động theo prompt người dùng qua Router; bổ sung **Input Filter Layer** tách
riêng để làm sạch dữ liệu đầu vào trước khi đưa vào LLM/router; bổ sung **Learning Loop**
thu thập feedback + auto-critique sau mỗi task và tự tối ưu routing rules/knowledge theo
chu kỳ định kỳ. Deploy bằng docker-compose production profile: restart policy, healthcheck,
migration tự động, cloud LLM (không host Ollama).

Nguyên tắc giữ nguyên: registry-driven routing, mọi LLM call qua `LLMProvider`,
n8n chỉ là integration, contracts typed, web tools qua `packages/tools` (ADR-008).

[Types]
Thêm trong `packages/contracts/models.py`:
- `FilteredInput(BaseModel)`: `clean_text: str`, `original_text: str`, `language: str`,
  `is_spam: bool`, `blocked: bool`, `block_reason: str | None`, `pii_masked: bool`,
  `metadata: dict[str, Any]` (length, url_count, fingerprint hash).
- `TaskFeedback(BaseModel)`: `task_id: UUID`, `organization_id: str | None`,
  `rating: Literal["up", "down"] | None`, `corrected_capability: str | None`,
  `comment: str | None`, `source: Literal["telegram", "dashboard", "api"]`,
  `auto_critique: dict[str, Any] | None`, `created_at: datetime`.


[Files]
**File mới:**
1. `packages/core/input_filter.py` — Input Filter Layer.
2. `packages/core/learning.py` — Learning Engine.
3. `packages/core/reflection.py` — LLM auto-critique.
4. `apps/api/routes/feedback.py` — `POST /v1/feedback`, `GET /v1/feedback/stats`.
5. `migrations/versions/000X_task_feedback.py` — bảng `task_feedback` (JSONB auto_critique,
   index trên corrected_capability + created_at).
6. `docker-compose.prod.yml` — production stack: restart: always, resource limits,
   Neon DATABASE_URL, migration job, không host Ollama (ADR-001), log rotation.
7. `scripts/run_production.sh` — `alembic upgrade head && uvicorn --workers 2`.
8. `docs/adr/ADR-009-input-filter-layer.md`, `docs/adr/ADR-010-learning-loop.md`.

**File sửa:**
- `packages/core/bootstrap.py` — tạo InputFilter + LearningEngine, inject vào orchestrator;
  env flag `AGENT_ENABLED_*` bật/tắt từng agent (mặc định tất cả bật).
- `packages/core/orchestrator.py` — `execute()` gọi filter trước `classify()`;
  spam/blocked trả REJECTED kèm block_reason, không tốn LLM call.
- `packages/core/graph.py` — node `filter_input` trước `classify_node`.
- `packages/core/router.py` — mở rộng `RULE_FALLBACKS` cho đủ domain (research, youtube,
  gmail, calendar, reporting, supply_chain, context); nhận `dynamic_rules` qua constructor.
- `packages/config/settings.py` — `INPUT_FILTER_ENABLED`, `INPUT_MAX_CHARS`,
  `PII_MASKING_ENABLED`, `LEARNING_ENABLED`, `LEARNING_CRON`.
- `apps/api/main.py` — feedback router + start LearningScheduler trong lifespan.
- `agents/monitoring/scheduler.py` — job learning loop `run_cycle()` (mặc định 03:00).
- `.env.example` — biến mới.

[Functions]
- `async def filter_input(text, *, settings) -> FilteredInput` —
  `packages/core/input_filter.py`. Pipeline: trim/normalize unicode → strip control chars
  → cap `INPUT_MAX_CHARS` (8000) → spam/empty detection → prompt-injection detection
  ("ignore previous instructions", role tokens) → mask PII (email, SĐT VN, CCCD, thẻ)
  → language detect (diacritics ratio). Pure Python, zero-cost, chạy trước mọi LLM call.
- `def mask_pii(text) -> tuple[str, bool]` — regex, giữ một phần format (`a***@gmail.com`).
- `def detect_injection(text) -> tuple[bool, str | None]` — pattern list, mở rộng từ
  dynamic rules của learning.
- `async def record_feedback(feedback) -> None` — LearningEngine, persist `task_feedback`.
- `async def run_critique(task_id, request, response) -> dict` — ReflectionEngine,
  structured LLM (score 0-1 + issues), MockLLM-safe.
- `async def run_cycle(session_factory) -> dict` — LearningEngine: tổng hợp feedback down
  + critique thấp → cập nhật dynamic routing rules từ `corrected_capability` → ingest
  comment vào knowledge pipeline → report dict lên Telegram.
- Sửa `RouterAgent.classify_text` — load dynamic rules trước rule constants.
- Sửa `Orchestrator.execute` — thêm bước filter đầu pipeline, signature giữ nguyên.

[Classes]
- `class InputFilter` — `packages/core/input_filter.py`. `async filter(text) -> FilteredInput`.
- `class LearningEngine` — `packages/core/learning.py`. `(session_factory, llm, settings)`.
  Methods: `record_feedback`, `load_dynamic_rules`, `run_cycle`. Tạo trong `build_container`.
- `class ReflectionEngine` — `packages/core/reflection.py`. `(llm, settings)`;
  `async critique(request, response)`; fire-and-forget sau task SUCCESS/FAILED.
- `class LearningScheduler` — asyncio task trong `agents/monitoring/scheduler.py`.
- Không xóa class nào; `Orchestrator`, `GraphOrchestrator`, `RouterAgent` sửa tối thiểu.

[Dependencies]
- KHÔNG thêm package bắt buộc (language detect + PII bằng regex tự viết).
- Production LLM: `cloudflare_ai` (Llama 70B) hoặc `external_openai_compatible` — đã có.

[Testing]
- `tests/unit/test_input_filter.py` — normalize, cap length, spam, injection, PII mask
  (email/SĐT VN/CCCD), unicode, empty input.
- `tests/unit/test_learning.py` — persist feedback, dynamic rules, run_cycle (mock).
- `tests/unit/test_reflection.py` — critique mock LLM, fire-and-forget.
- `tests/integration/test_feedback_api.py` — 201/401/422 + GET stats.
- Sửa `tests/unit/test_router.py` (dynamic rules), `test_router_api.py`, `test_api.py`.
- Validation: pytest + ruff + compileall + smoke production compose → /health, /ready,
  1 task E2E qua /v1/router với prompt tự do → route đúng agent.

[Implementation Order]
1. Contracts (FilteredInput, TaskFeedback) + settings.
2. `input_filter.py` + unit tests.
3. `router.py`: RULE_FALLBACKS mở rộng + dynamic_rules + tests.
4. Wire filter vào Orchestrator + graph node + integration tests.
5. Migration `task_feedback` + feedback API + tests.
6. `reflection.py` + hook + tests.
7. `learning.py` + scheduler job + tests.
8. Production infra: compose override, run script, .env.example, ADR-009/010, README.
9. Full gate: pytest + ruff + compileall + smoke E2E.
