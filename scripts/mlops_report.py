"""MLOps report — reads evaluations + agent_runs from DB, prints health.

Usage:
    python scripts/mlops_report.py

Minimal MLOps loop for this swarm:
    1. Every agent run is logged in agent_runs (already wired by orchestrator).
    2. task_feedback (👍/👎) accumulates human eval signal.
    3. This report surfaces success rate + feedback ratio; a cron can push it
       to the `evaluations` table for drift tracking over time.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select, text

from packages.config.settings import get_settings
from packages.database.models import AgentRun
from packages.database.session import get_session_factory


async def report() -> None:
    factory = get_session_factory(get_settings())
    async with factory() as session:
        total = (await session.scalar(select(func.count(AgentRun.id)))) or 0
        ok = (
            await session.scalar(
                select(func.count(AgentRun.id)).where(AgentRun.status == "success")
            )
        ) or 0
        row = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END), 0) AS up, "
                    "COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0) AS down "
                    "FROM task_feedback"
                )
            )
        ).first()
        up, down = (row.up or 0, row.down or 0) if row else (0, 0)
    success_rate = ok / total * 100 if total else 0.0
    print("=== MLOps Report ===")
    print(f"Agent runs:      {total} (success {ok}, {success_rate:.1f}%)")
    print(f"Human feedback:  👍 {up} / 👎 {down}")
    if total == 0:
        print("(empty — chats will populate agent_runs as the bot is used)")
    # Alert thresholds (simple drift detection)
    if total >= 20 and success_rate < 80:
        print("⚠️ SUCCESS RATE < 80% — investigate failing agents!")
    if down > up and (up + down) >= 10:
        print("⚠️ Negative feedback majority — review recent replies")


if __name__ == "__main__":
    asyncio.run(report())
