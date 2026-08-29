# Báo cáo Task 4 — Email-to-Proposal Automation

**Branch:** `main` (repo dùng `main`, không phải `master` — plan ghi master nhưng thực tế git branch là `main`; commit lên `main` theo convention hiện hành).
**Trạng thái:** ✅ Hoàn thành — tests xanh, py_compile sạch, ruff không lỗi E9xx/F821, đã commit `[verified]`.
**LLM:** capability `sales.process_email` chạy hoàn toàn offline (không cần model), LLM chỉ inject tùy chọn, không bắt buộc.

## 1. Mục tiêu
Tự động hóa: đọc email khách hỏi dịch vụ → phân loại intent → soạn báo giá + proposal (template) → xuất PDF có branding (reportlab, offline) → soạn email follow-up. Mô phỏng video TikTok "Claude xử lý email khách → soạn báo giá + proposal + PDF branding + follow-up".

## 2. Các file đã tạo / sửa

### Mới
- **`agents/sales/agent.py`** + **`agents/sales/__init__.py`**
  - `SalesAgent` với capability `sales.process_email` (domain `Domain.SALES` mới).
  - `process_email(email_text, brand, client, package_key) -> ProposalResult`: phân loại intent (hỏi dịch vụ / báo giá / khiếu nại) qua keyword VN+EN; trích xuất client từ chữ ký email; chọn gói (Launch Impact / Growth Boost / Starter) qua keyword; sinh proposal (template `data/templates/proposal.md` + giá từ `data/templates/pricing.json`); soạn follow-up email tiếng Việt (khác nội dung cho khiếu nại vs báo giá).
  - `render_pdf(proposal, brand) -> bytes`: xuất PDF branding bằng **reportlab** Platypus (offline, pure-python). Dùng font Arial (C:/Windows/Fonts) để render tiếng Việt có dấu; fallback Helvetica nếu không có font. Màu sắc lấy từ `brand.json`.
  - `handle()`: capability envelope — trả `result` chứa `proposal_markdown`, `price`, `currency`, `follow_up`, `pdf_bytes`, `pdf_size`. Reject thiếu email / sai action; FAILED nếu lỗi (không bịa).
  - Helpers: `classify_intent`, `load_brand`, `load_pricing`, `render_pdf` (module-level, dễ test).
- **`data/templates/proposal.md`** — template "Launch Impact" style với placeholder `{{client}}`, `{{scope}}`, `{{timeline}}`, `{{price}}`, `{{currency}}`, `{{brand_company}}`, `{{brand_tagline}}`, v.v.
- **`data/templates/pricing.json`** — 3 gói (Launch Impact 180tr / Growth Boost 120tr / Starter 45tr), `currency`, `default_package`, `validity_days`.
- **`data/brand/brand.json`** — config branding placeholder (tên công ty, màu primary/accent, logo path placeholder, contact).
- **`tests/unit/test_sales_agent.py`** — 21 test, chạy nhanh, không network:
  - `classify_intent` (parametrize quote/service/complaint/other).
  - Agent đăng ký `sales.process_email` (domain `sales`).
  - `process_email`: đúng client/scope/timeline/price (180tr từ pricing.json)/currency/follow-up; khiếu nại có follow-up khác; default package; override client/package.
  - `render_pdf`: trả bytes **non-empty**, bắt đầu bằng `%PDF-` (PDF hợp lệ); test trực tiếp hàm module.
  - `handle()`: SUCCESS kèm `pdf_bytes`, REJECTED thiếu email / sai action.
  - Registry resolve `sales.process_email` → SalesAgent; bootstrap container đăng ký sales agent.

### Sửa
- **`packages/contracts/enums.py`** — thêm `Domain.SALES = "sales"`.
- **`packages/core/bootstrap.py`** — import + register `create_sales_agent(llm=llm)` (không đổi Task1/2/3).
- **`packages/core/router.py`** — thêm capability keyword `(báo giá, quote, proposal, đề xuất, chào giá, email khách, báo gia) -> sales` cho free-text routing.
- **`agents/monitoring/telegram_bot.py`** — **chỉ thêm, không sửa block Task1/2/3**:
  - Đăng ký `CommandHandler("sales", self._sales_command)` (sau `/advisory`).
  - `_sales_command`: `/sales <email_text|email_id>` → gọi `sales.process_email`, trả tóm tắt + **gửi file PDF document** (reply_document). Nếu arg trông như Gmail id thì thử `gmail.search` trước.
  - Trong `_message_handler` (free-text): thêm block 2d — nếu text chứa từ khóa sales (báo giá/proposal/quote/...) thì route sang `_sales_command`.
  - Cập nhật `/help` thêm dòng `/sales`.
- **`pyproject.toml`** — thêm `"reportlab>=4.0,<6.0"` (Task 4: offline PDF, Ruling reportlab không weasyprint). **`uv.lock`** cập nhật.

> Lưu ý: `.gitignore` ignore toàn bộ `data/`. Để feature tự chứa, 3 file data của Task 4 được `git add -f` (force-add) nên được commit cùng. (Task 1 cũng lưu data trên disk nhưng chưa tracked — nhất quán với cách này.)

## 3. Xác minh (Verification gate)
- `python -m py_compile` các file mới/sửa → sạch.
- `ruff check --select E9,F821` → **All checks passed** (không E9xx/F821).
- `ruff check` (full) trên file Task 4 → sạch (import order OK).
- `pytest tests/unit/test_sales_agent.py` → **21 passed**.
- Regression: `pytest tests/unit` toàn bộ → **455 passed** (không lỗi Task1/2/3).
- Import toàn bộ (`agents.sales`, `packages.core.bootstrap`, `agents.monitoring.telegram_bot`) → OK.
- PDF thực tế sinh được (`%PDF-` header, bytes > 500).

## 4. Commit
Trên `main` — message: `[verified] Task 4: Email-to-Proposal Automation (sales agent + reportlab PDF + brand config + /sales telegram route)`.
**Không push** (Global Constraints: repo không có remote mặc định push; user sẽ push sau).

## 5. Lưu ý / quyết định
- Pipeline deterministic, không phụ thuộc LLM/network → đúng Ruling "offline, pure-python".
- Branding dùng `data/brand/brand.json` placeholder (logo path chưa có file thật) — render vẫn chạy (không cần logo).
- Font Arial được ưu tiên để PDF tiếng Việt có dấu trên Windows; trên môi trường thiếu Arial sẽ fallback Helvetica (PDF vẫn valid, dấu có thể không render).
- Router free-text cho sales đặt sau block advisory (2c), không overlap với Task1/2/3.
