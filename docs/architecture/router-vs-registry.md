# Phase 4 — Router Agent: phân biệt với registry routing hiện có

## Hiện trạng (Phase 0/1): registry-driven routing
- Caller **phải chỉ định tường minh** `domain` + `action` trong request
  (`TaskRequest.domain`, `TaskRequest.action`). Ví dụ: caller phải tự biết
  gửi `"domain": "support", "action": "triage"`.
- `Orchestrator.classify()` chỉ ghép chuỗi `f"{domain}.{action}"` rồi tra
  capability trong `AgentRegistry.get_by_capability()`. LLM call trong classify
  hiện là placeholder (không ảnh hưởng kết quả).
- Registry là bảng tra cứu tĩnh: agent nào đăng ký capability nào thì nhận task đó.
- Hạn chế: toàn bộ "trí tuệ điều phối" nằm ở **caller** (n8n workflow, dashboard).
  Với email thô của khách, không ai biết nên route sang support hay knowledge.

## Phase 4 thêm: Router Agent (intent classification)
- Đầu vào: **nội dung tự do** (`text` thô — ví dụ email khách hàng), KHÔNG có
  domain/action. Đây là khác biệt cốt lõi: router suy luận intent từ nội dung.
- Cơ chế:
  1. LLM structured classification → `{domain, action, confidence}` theo danh sách
     intent đóng (enum giới hạn từ capability registry, không tự do).
  2. Rule-based fallback/override: từ khoá mạnh (VD: "refund", "hóa đơn")
     map trực tiếp intent khi LLM fail hoặc confidence thấp — luôn deterministic,
     không bao giờ fail-closed vào 1 agent mặc định.
  3. Confidence thấp (< ngưỡng) → ESCALATED cho human, không đoán.
- Output: một TaskRequest nội bộ có domain+action đã điền → đi vào orchestrator
  pipeline HIỆN CÓ (registry routing giữ nguyên vai trò tra cứu capability).

## Phân công trách nhiệm sau Phase 4
| Lớp | Trách nhiệm |
|---|---|
| Router Agent (mới) | text thô → intent (domain.action) + confidence |
| Orchestrator.classify | giữ nguyên: validate capability tồn tại |
| AgentRegistry | giữ nguyên: tra cứu handler theo capability |

## Ranh giới (YAGNI)
- Không thay đổi `POST /v1/tasks` hiện có; thêm endpoint riêng
  `POST /v1/router/dispatch` nhận `{"text": ...}`.
- Không multi-hop handoff phức tạp: 1 lần classify → 1 agent chính; handoff
  knowledge-query bên trong support flow đã xử lý ở tầng tool/agent call.
- Router intents giới hạn cho use case pilot email CS:
  - `support.triage` (phân loại/mức ưu tiên)
  - `support.draft_reply` (soạn trả lời)
  - `knowledge.query` (câu hỏi chính sách/FAQ)
  - fallback: escalate.

## Acceptance criteria
1. Email "Tôi muốn hoàn tiền đơn #123" → `support.triage` (rule hoặc LLM).
2. Text vô nghĩa / confidence < ngưỡng → ESCALATED, không chọn agent bừa.
3. LLM provider crash → rule fallback vẫn route đúng các intent phổ biến.
4. Toàn bộ qua test với MockLLMProvider, không cần network.
