# Task 2 — Business Ops Hub: Fix Report

## Fix Round 2 (R2) — Unify SchedulerConfig timezone

**Issue (latent, discovered in R1 re-review):** Two `SchedulerConfig` classes
existed with conflicting timezones. `agents/monitoring/scheduler.py` correctly
used `Asia/Ho_Chi_Minh` (UTC+7, the actual Business Ops Hub runtime), but
`agents/monitoring/config.py:41` still declared `Asia/Seoul` (UTC+9). Because
`config.py` is the settings model consumed by the app, any code path importing
`agents.monitoring.config.SchedulerConfig` would run scheduling on Seoul time —
a 2-hour drift versus the intended VN schedule. The original R1 test only
asserted the `scheduler.py` class, giving a false-positive pass.

**Fix:** Changed `agents/monitoring/config.py:41` from `Asia/Seoul` to
`Asia/Ho_Chi_Minh` so both `SchedulerConfig` classes agree on the VN timezone.

**Verification:**
- `py_compile` on config.py, scheduler.py, test_ops_hub.py — clean.
- `ruff check --select E9,F821` — All checks passed.
- `pytest tests/unit/test_ops_hub.py` — 15 passed.
- Grep for `Asia/Seoul` — only remains in test comments; no live usage.
- Added test `test_config_scheduler_timezone_is_vn` asserting
  `agents.monitoring.config.SchedulerConfig().time_zone == "Asia/Ho_Chi_Minh"`,
  alongside the existing `test_scheduler_ops_hub_job_uses_vn_timezone`.

**Commit:** `[verified] fix(ops): unify scheduler timezone to Asia/Ho_Chi_Minh in config.py`
