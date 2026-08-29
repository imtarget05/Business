# ADR-008: Web tools provider abstraction — hermes is optional, never required

**Status:** Accepted (2026-08-29)

## Context

Các agent cần web search / web extract (`agents/research`, `agents/youtube`,
`agents/monitoring/research`). Trước ADR này logic được copy-paste ở 3 nơi với
`try: from hermes_tools import ...` và hành vi fallback không nhất quán
(một chỗ raise RuntimeError khi thiếu hermes, một chỗ có fallback httpx/mock).

## Decision

1. Tạo `packages/tools/web.py` với protocol `WebToolsProvider` và factory
   `create_web_tools(provider)` — tương tự mô hình `LLMProvider` (ADR-005).
2. Chuỗi degrade khi `provider="auto"` (mặc định): **hermes → httpx → mock**.
   - `hermes`: adapter mỏng qua `hermes_tools` (chỉ tồn tại trong Hermes runtime).
   - `httpx`: DuckDuckGo HTML search + page fetch — chạy standalone, không SDK.
   - `mock`: deterministic, zero network — dùng cho test/dev.
3. Agents nhận tool qua constructor injection (`web_tools=`), business logic
   KHÔNG import `hermes_tools` trực tiếp nữa.
4. `hermes_tools` không được thêm vào `pyproject.toml` dependencies.

## Consequences

- Hệ thống chạy hoàn toàn không cần hermes (httpx fallback / mock).
- Trong Hermes runtime, quality search tốt hơn tự động được dùng qua `auto`.
- Tránh xóa file trùng `agents/monitoring/research.py` (dead code bị package
  cùng tên che khuất) — đã loại bỏ để hết xung đột import.
