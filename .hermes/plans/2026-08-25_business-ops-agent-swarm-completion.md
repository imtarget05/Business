# Business Ops Agent Swarm — Kế hoạch hoàn thành dự án (Phase 2–5)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Hoàn thành một nền tảng multi-agent (agent swarm) tự động hoá nghiệp vụ cho doanh nghiệp — theo hướng của 2 video tham khảo: (1) "Building an AI Agent Swarm in n8n" — router/orchestrator điều phối nhiều agent chuyên trách; (2) workflow AI automation thực chiến (Nate Herk) — agent gắn với công cụ thật (email, sheet, CRM, webhook) để giải quyết đúng 1 vấn đề nghiệp vụ.

**Architecture:** FastAPI orchestrator định tuyến task theo domain/capability tới các domain agent; mỗi agent dùng LLM provider abstraction + tools (retrieval, email, CRM…). n8n là lớp integration bên ngoài (Slack/Gmail/webhook → API). Next.js dashboard là control plane. PostgreSQL + pgvector lưu state và knowledge.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 + Alembic, pgvector, Pydantic v2, Next.js + Tailwind, n8n, Docker.

---

## Hiện trạng (Phase 1 đã xong)

- Orchestrator skeleton, Agent Registry, agent protocol (`packages/core`)
- Contracts typed (`packages/contracts`), LLM provider abstraction: mock | cloudflare_ai (`packages/llm`)
- DB models + Alembic migrations (đang có migration 0003 knowledge_documents chưa commit)
- Dashboard shell Next.js có live views; n8n inbound-task-relay workflow
- Auth cơ bản trên API
- Agents hiện có: `knowledge`, `support`

## Định nghĩa "hoàn thành" (Definition of Done)

Một doanh nghiệp nhỏ có thể:
1. Gửi yêu cầu từ Slack/Gmail/form qua n8n webhook vào API.
2. Router phân loại và điều phối tới đúng agent (support / knowledge / ops).
3. Agent tra cứu knowledge base (RAG/pgvector), gọi tool thật (gửi email reply, ghi CRM/sheet), trả kết quả.
4. Toàn bộ task được log/trace trên dashboard; có thể xem lại hội thoại và kết quả.
5. Chạy end-to-end bằng docker-compose, có test coverage và CI xanh.

---

## Phase 2 — Knowledge Agent hoàn chỉnh (RAG)

### Task 2.1: Commit dọn Phase 1
- Review + commit các file đang modified/untracked (migration 0003, mock_embedding).
- Run: `pytest -q` → PASS rồi `git commit`.

### Task 2.2: Ingestion pipeline
- Files: `agents/knowledge/ingest.py`, `packages/database/repositories/documents.py`
- Chunking (≈800 tokens, overlap 100) → embedding qua provider → lưu `knowledge_documents` + vectors.
- Endpoint: `POST /v1/knowledge/ingest` (text/url upload).
- Test: `tests/unit/test_ingest.py` — chunk count, embedding dims, upsert idempotent.

### Task 2.3: Retrieval (semantic search)
- `agents/knowledge/retriever.py`: pgvector cosine similarity top-k.
- Test: fixture 3 documents, query khớp đúng doc liên quan.

### Task 2.4: Knowledge Agent answer loop
- Sửa `agents/knowledge/agent.py`: retrieve → prompt LLM kèm context → trả answer + citations.
- Test integration với mock LLM provider.

### Task 2.5: Dashboard page Knowledge
- `apps/web`: trang ingest + hỏi đáp, hiển thị citations.

Commit sau mỗi task: `feat(knowledge): ...`

---

## Phase 3 — Support Agent + Tool Use

### Task 3.1: Tool protocol trong core
- `packages/core/tools.py`: interface `Tool` (name, schema, run) + registry per-agent; LLM provider thêm method `complete_with_tools()`.
- Test: mock tool được gọi đúng khi LLM trả tool_call.

### Task 3.2: Conversation persistence
- Bảng `conversations` + `messages`; support agent duy trì thread đa lượt.
- Migration mới + repository + tests.

### Task 3.3: Tools cho support agent
- `send_email_reply` (SMTP/API, dry-run mode mặc định), `create_ticket`, `lookup_customer` (CRUD bảng customers đơn giản).
- Mỗi tool: unit test riêng + test agent gọi chuỗi tool.

### Task 3.4: Support endpoint
- `POST /v1/conversations/{id}/messages` → agent xử lý → reply + actions đã thực thi.
- Test e2e với mock LLM: câu hỏi → tool lookup → trả lời.

### Task 3.5: Dashboard inbox view
- Trang hội thoại: danh sách thread, chat view, badge action đã chạy.

---

## Phase 4 — Router/Swarm orchestration kiểu video 1

### Task 4.1: Router agent (classifier)
- `packages/core/router.py`: LLM-based intent classification → chọn domain agent theo capability registry; fallback rule-based khi LLM fail.
- Test: routing table + mock LLM trả label.

### Task 4.2: Multi-agent handoff
- Orchestrator hỗ trợ chuỗi: router → agent A → (optional) delegate sang agent B, truyền state qua contracts.
- Test: task `support+knowledge` được handoff đúng.

### Task 4.3: Task lifecycle + retry
- Trạng thái task: queued → running → completed/failed; timeout, 1 retry, dead-letter.
- Test failure path với fake failing agent.

---

## Phase 5 — Integrations & Go-live cho 1 doanh nghiệp cụ thể

### Task 5.1: Chọn bài toán + khách hàng thí điểm (quyết định cần user)
- Đề xuất mặc định: **tự động trả lời & phân loại email/hỗ trợ khách hàng cho 1 shop dịch vụ** (giảm thời gian phản hồi, không bỏ sót ticket).

### Task 5.2: n8n inbound hoàn chỉnh
- Gmail trigger / Slack bot → HTTP Request → `POST /v1/conversations` ; reply từ API → gửi ngược email/Slack.
- Cập nhật `integrations/n8n/*.json` + README hướng dẫn import.

### Task 5.3: Auth + rate limit production-grade
- API key per tenant (thay auth cơ bản), rate limiting đơn giản (slowapi hoặc middleware tự viết).
- Tests: 401 sai key, 429 khi vượt limit.

### Task 5.4: Observability
- Structured logs đã có; bổ sung: trace từng step của agent vào bảng `task_events`, dashboard page hiển thị timeline.
- Test: mỗi lần chạy agent tạo ≥ N events đúng thứ tự.

### Task 5.5: Deployment
- `docker-compose.yml` đầy đủ (api, web, db+pgvector, n8n); env template; script seed data demo.
- Smoke test: `docker compose up` → health OK → chạy 1 task end-to-end qua n8n webhook.

### Task 5.6: CI
- GitHub Actions: ruff + mypy + pytest trên mỗi push.
- Badge vào README.

---

## Kiểm thử tổng thể

- `pytest -q` toàn repo xanh sau mỗi phase.
- E2E cuối: curl webhook n8n → task → agent → email draft hiển thị trên dashboard.
- Load nhẹ: 20 task đồng thời qua `/v1/tasks` không rơi connection.

## Rủi ro & tradeoff

- **LLM thật vs mock:** mọi test dùng mock/cloudflare để không phụ thuộc quota; cần 1 bước verify thủ công với LLM thật trước go-live.
- **Email auto-reply rủi ro uy tín:** mặc định dry-run (draft-only), bật gửi thật bằng flag config sau khi review.
- **Phạm vi lan man:** giữ YAGNI — chỉ 1 use case doanh nghiệp làm chuẩn, agent khác chỉ mở rộng sau.
- **pgvector local dev:** cần docker; fallback sqlite không hỗ trợ vector nên test retrieval phải chạy với postgres testcontainer hoặc Neon branch.

## Câu hỏi mở

1. Doanh nghiệp/use case thí điểm cụ thể là gì? (Task 5.1)
2. Có tài khoản Cloudflare AI thật để verify chất lượng LLM trước go-live không?
3. Email channel ưu tiên: Gmail hay Outlook?

Đã lưu kế hoạch. Sẵn sàng thực thi theo subagent-driven-development — mỗi task một subagent với review 2 lớp (spec compliance rồi code quality). Bạn muốn bắt đầu từ Phase 2 và xác nhận use case ở Task 5.1 chứ?
