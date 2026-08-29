# Báo cáo Task 5 — Competitive Intelligence (Tình báo cạnh tranh)

**Ngày:** 30/08/2026
**Branch:** master
**Trạng thái:** ✅ Hoàn thành — tests xanh, commit `[verified]`

## Mục tiêu
Triển khai hệ thống tình báo cạnh tranh theo luồng TikTok:
**COLLECT** (thu thập bài đăng/giá đối thủ) → **ANALYZE** (phân tích pattern/dịch chuyển) → **WEEKLY BRIEF** (bản tóm tắt hàng tuần).

## Các file đã tạo / sửa

### Tạo mới
- `agents/competitor/__init__.py` — package export.
- `agents/competitor/agent.py` — `CompetitorAgent` với:
  - `collect(queries)` → dùng `packages/tools/web.py` `web_search` (KHÔNG LLM tự crawl, chỉ search + parse title/url/snippet). Trả `list[CompetitorSignal]`.
  - `analyze(signals)` → nhóm theo đối thủ, phát hiện dịch chuyển giá + pattern (heuristic); tóm tắt nhẹ bằng LLM nếu có, **fallback heuristic nếu LLM lỗi** (không bao giờ raise/giả).
  - `weekly_brief(org_id, competitor?)` → Markdown ngắn gọn (<400 từ, tiếng Việt): Top movers / Dịch chuyển giá / Chủ đề / Tóm tắt / Đề xuất.
  - Heuristic trích giá VN-aware: `1.200.000 VND`, `250k`, `19 USD`, `2 triệu`.
  - Capabilities: `competitor.brief` (+ `competitor.collect`), domain `Domain.COMPETITOR`.
- `data/competitor/competitors.json` — config placeholder (DoiThuA/DoiThuB + queries mẫu; user điền sau). Đã `git add -f` (vì `data/` bị gitignore, tương tự Task 4).
- `tests/unit/test_competitor_agent.py` — 18 test, mock `web_search`, **không network**.

### Sửa
- `packages/contracts/enums.py` — thêm `Domain.COMPETITOR`.
- `packages/core/router.py` — thêm keyword `đối thủ/competitor/cạnh tranh` → `competitor`.
- `packages/core/bootstrap.py` — import + register `CompetitorAgent` vào container.
- `agents/monitoring/scheduler.py` — job `competitor_weekly` **Thứ 2 09:00 Asia/Ho_Chi_Minh** → gọi `competitor.brief` → push Telegram.
- `agents/monitoring/telegram_bot.py` — lệnh `/compete [tên đối thủ]` (block MỚI, không sửa Task 1-4) + thêm menu help.

## Ràng buộc đã tuân thủ
- `collect` chỉ dùng `web_search` (theo `packages/tools/web.py`), không LLM crawl.
- `analyze` dùng LLM nhẹ (container.llm) để tóm tắt, có fallback heuristic khi LLM fail.
- `weekly_brief` Markdown <400 từ tiếng Việt.
- `Domain.COMPETITOR` thêm vào enum; router + bootstrap + scheduler + telegram đều đã nối.
- `/compete` là block mới, giữ nguyên các block Task 1-4.
- `data/competitor/competitors.json` là placeholder config.

## Verification
- `py_compile` sạch trên các file chạm tới.
- `ruff` không lỗi E9xx/F821 (file mới sạch hoàn toàn: `All checks passed!`).
- `pytest tests/unit/test_competitor_agent.py` → **18 passed**.
- Import sanity: `agents.monitoring.scheduler`, `telegram_bot`, `bootstrap`, `router`, `agents.competitor` đều import OK.

## Lưu ý
- `data/` bị gitignore; file `data/competitor/competitors.json` được force-add (như `data/templates/*` của Task 4) để config tồn tại trong repo.
- Scheduler không push remote (theo quy ước repo: chỉ commit local, user tự push sau).
- Chưa có remote → KHÔNG push.

## Brief mẫu (chạy offline, competitor DoiThuA/DoiThuB trong config placeholder)
```
📊 Weekly Competitive Brief  (30/08/2026)

🚀 Top movers:
• DoiThuA: 1 tín hiệu
• DoiThuB: 1 tín hiệu

💰 Dịch chuyển giá:
• ↑ DoiThuA 1.200.000 VND — DoiThuA ra mắt gói mới giá 1.200.000 VND
• ↓ DoiThuB 250.000 VND — DoiThuB giảm giá khuyến mãi 250k

🔎 Chủ đề nổi bật: ra mắt, đối tác

🧭 Tóm tắt:
Phát hiện 2 tín hiệu từ 2 đối thủ. ...

✅ Đề xuất:
• Đối thủ đang giảm giá — xem xét ưu đãi đi kèm (value-add) thay vì hạ giá.
• Đối thủ ra mắt tính năng/sản phẩm mới — rà soát USP và nội dung đối chiếu.
```
