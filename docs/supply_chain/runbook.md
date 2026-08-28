# Supply Chain Agent Swarm — Runbook

Vận hành pipeline PO: `po_agent → approval → inventory → reporting → n8n_export`.

## Một PO đi qua các bước nào

1. **po_agent** — parse email → struct PO (`po_number`, `vendor`, `items`, `total`, `route`).
   - `route = auto_approved` nếu total < `po_approval_thresholds.manager_a` ($500).
   - `approval_required_manager_a` ($500–$5000), `approval_required_manager_b` (>$5000).
2. **approval** — nếu cần, chuyển sang `PENDING_HUMAN_APPROVAL` (notification là STUB, chờ tích hợp email/Slack).
3. **inventory** — kiểm tra tồn kho, sinh alerts (low/out/over-stock).
4. **reporting** — tổng hợp dashboard (po_metrics, inventory_metrics).
5. **n8n_export** — đẩy PO đã approve sang n8n webhook (nếu `N8N_WEBHOOK_URL` được set).

Mỗi node được bọc bởi 3-tier guardrails + **CircuitBreaker** (tự mở lại sau 30s nếu fail liên tiếp).

## Cấu hình (`.env` / `settings.py`)

| Biến | Mặc định | Ý nghĩa |
|------|----------|--------|
| `LLM_PROVIDER` | `ollama` | `mock` / `ollama` / `cloudflare_ai` / `external_openai_compatible` |
| `LLM_MODEL` | `qwen2.5:3b` | Phải khớp model đã pull trên máy |
| `PO_APPROVAL_THRESHOLDS` | `{"manager_a":500,"manager_b":5000}` | Ngưỡng duyệt PO |
| `N8N_WEBHOOK_URL` | (trống) | Để trống = n8n_export no-op (không crash) |
| `TRACING_BACKEND` | (trống) | `langfuse` / `otel` / không set = no-op |

## LLM fallback (Phase F)

Khi provider thật fail (timeout/429/unreachable), factory tự chuyển:
`Ollama → Cloudflare/Nous Cloud → Mock`. Mock luôn là chốt cuối, hệ thống không bao giờ hard-fail.

## Monitoring (Phase B)

- Health check: `python -m agents.monitoring.health_check`
- Scheduler (health mỗi 30p, report 09:00): `python -m agents.monitoring.scheduler`
- Telegram: set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, gửi `/health` `/report` `/research`.

## Alert

- Health `overall != ok` → scheduler push cảnh báo Telegram (nếu cấu hình).
- n8n_export fail → ghi log warning, PO vẫn success (export là non-blocking).
- Approval pending quá `timeout_seconds` → state `EXPIRED`.

## Rollback / Recovery

- **PO parse sai** → `po_agent` trả `failed` với `PARSE_ERROR`; không vào pipeline.
- **Approval cần con người** → state `PENDING_HUMAN_APPROVAL`; resume sau khi `resolve(decision=...)` được gọi.
- **Circuit breaker mở** → node tương ứng fail fast, trả error rõ ràng thay vì treo.
- **n8n chết** → PO vẫn xử lý xong; chỉ bước export bị skip (no-op).

## Chạy test

```bash
pytest tests/integration/test_supply_chain_n8n.py   # n8n + circuit breaker
pytest tests/integration/test_supply_chain_graph_e2e.py  # full pipeline
pytest tests/integration/test_supply_chain_inbound.py  # inbound email
```
