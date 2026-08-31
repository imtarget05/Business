"""Progress report module — generates daily/weekly reports from system data.

Collects data from:
- Database: task records (tasks table via SqlAlchemyTaskStore)
- Agent registry: agent execution stats (via container if available)
- Health check: system status snapshot

Generates markdown report with summary statistics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import get_settings
from packages.database.models import Task, TaskStatusDB
from packages.database.session import get_session_factory
from packages.database.task_store import _task_to_dict

logger = logging.getLogger(__name__)


async def get_rag_stats() -> dict[str, Any]:
    """Count verified Michelin facts cached in the local RAG store (pgvector/FTS)."""
    try:
        from sqlalchemy import text

        from packages.config.settings import get_settings
        from packages.database.session import get_session_factory

        s = get_settings()
        factory = get_session_factory(s)
        async with factory() as session:
            total = (
                await session.execute(text("SELECT count(*) FROM michelin_facts"))
            ).scalar() or 0
            recent_rows = (
                await session.execute(
                    text(
                        "SELECT question, created_at FROM michelin_facts "
                        "ORDER BY created_at DESC LIMIT 5"
                    )
                )
            ).all()
            recent = [
                {"question": r[0], "created_at": r[1].isoformat() if r[1] else ""}
                for r in recent_rows
            ]
            return {"total": int(total), "recent": recent}
    except Exception as e:  # pragma: no cover - DB optional
        logger.warning("RAG stats unavailable: %s", e)
        return {"total": 0, "recent": []}


def get_llm_cost_summary() -> dict[str, Any]:
    """Aggregate the LLM usage ledger if present (no DB needed)."""
    import os

    from packages.core import llm_cost

    path = os.environ.get("LLM_USAGE_LEDGER") or llm_cost._LEDGER
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return {"present": False, "calls": 0, "cache_hits": 0, "est_cost_usd": 0.0}
    calls = 0
    cache_hits = 0
    cost = 0.0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            calls += 1
            if row.get("cache_hit"):
                cache_hits += 1
            cost += float(row.get("est_cost_usd", 0.0) or 0.0)
    except Exception as e:  # pragma: no cover
        logger.warning("LLM ledger parse failed: %s", e)
        return {"present": True, "calls": calls, "cache_hits": cache_hits, "est_cost_usd": cost}
    return {
        "present": True,
        "calls": calls,
        "cache_hits": cache_hits,
        "est_cost_usd": round(cost, 6),
    }


async def get_health_snapshot() -> dict[str, Any]:
    """Lightweight health snapshot for the report (no network ping)."""
    try:
        from agents.monitoring.health_check import run_health_check

        h = await run_health_check()
        d = h.to_dict()
        return {
            "overall": d.get("overall", "unknown"),
            "components": d.get("checks", []),
        }
    except Exception as e:  # pragma: no cover
        logger.warning("Health snapshot unavailable: %s", e)
        return {"overall": "unknown", "components": []}


@dataclass
class DailyReport:
    """Aggregated daily report data."""

    date: str
    generated_at: str
    period_hours: int = 24

    # Task statistics
    total_tasks_created: int = 0
    total_tasks_completed: int = 0
    success_rate: float = 0.0
    pending_tasks: int = 0
    failed_tasks: int = 0
    dead_lettered_tasks: int = 0

    # Agent execution stats (placeholder)
    agent_stats: dict[str, Any] = field(default_factory=dict)

    # Recent task list (for report)
    recent_tasks: list[dict[str, Any]] = field(default_factory=list)

    # Knowledge / RAG cache stats
    rag_facts: int = 0
    rag_recent: list[dict[str, Any]] = field(default_factory=list)

    # LLM cost summary
    llm_calls: int = 0
    llm_cache_hits: int = 0
    llm_est_cost_usd: float = 0.0
    llm_ledger_present: bool = False

    # Health snapshot
    health_overall: str = "unknown"
    health_components: list[dict[str, Any]] = field(default_factory=list)

    # Raw data for serialization
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            f"# 📊 Daily Progress Report — {self.date}",
            "",
            f"**Generated**: {self.generated_at}",
            "",
            "## 📈 Task Statistics",
            "",
            "| Metric | Count |",
            "|--------|-------|",
            f"| Tasks Created (24h) | {self.total_tasks_created} |",
            f"| Tasks Completed | {self.total_tasks_completed} |",
            f"| Success Rate | {self.success_rate:.1%} |",
            f"| Pending Tasks | {self.pending_tasks} |",
            f"| Failed Tasks | {self.failed_tasks} |",
            f"| Dead-Lettered | {self.dead_lettered_tasks} |",
            "",
        ]

        if self.agent_stats:
            lines.append("## 🤖 Agent Statistics")
            lines.append("")
            for agent_name, stats in self.agent_stats.items():
                if isinstance(stats, dict):
                    lines.append(
                        f"- **{agent_name}**: {stats.get('executions', 0)} executions, "
                        f"{stats.get('success_rate', 0):.1%} success"
                    )
                else:
                    lines.append(f"- **{agent_name}**: {stats}")
            lines.append("")

        if self.recent_tasks:
            lines.append("## 🔍 Recent Tasks")
            lines.append("")
            lines.append("| Time | Action | Status |")
            lines.append("|------|--------|--------|")
            for task in self.recent_tasks[:15]:
                time = task.get("created_at", "")[:16] if task.get("created_at") else "N/A"
                action = task.get("action", "unknown")[:25]
                status = task.get("status", "unknown")
                lines.append(f"| {time} | {action} | {status} |")
            lines.append("")

        # Knowledge / RAG cache (real verified facts)
        lines.append("## 🧠 Knowledge Cache (RAG)")
        lines.append("")
        lines.append(f"- **Verified Michelin facts cached**: {self.rag_facts}")
        if self.rag_recent:
            lines.append("")
            lines.append("Recent:")
            for f in self.rag_recent[:5]:
                q = (f.get("question") or "")[:60]
                lines.append(f"  - {q}")
        lines.append("")

        # LLM cost (real ledger)
        lines.append("## 💰 LLM Cost")
        lines.append("")
        if self.llm_ledger_present:
            _hit_rate = (self.llm_cache_hits / self.llm_calls * 100) if self.llm_calls else 0.0
            lines.append(f"- **Calls**: {self.llm_calls}")
            lines.append(f"- **Cache hits**: {self.llm_cache_hits} ({_hit_rate:.1f}%)")
            lines.append(f"- **Est. cost**: ${self.llm_est_cost_usd:.6f}")
        else:
            lines.append("- *No usage recorded yet (ledger empty)*")
        lines.append("")

        # Health snapshot
        lines.append("## 🏥 Health")
        lines.append("")
        lines.append(f"- **Overall**: {self.health_overall}")
        if self.health_components:
            lines.append("")
            for c in self.health_components:
                lines.append(f"  - {c.get('name')}: {c.get('status')} — {c.get('message')}")
        lines.append("")

        lines.append("---")
        lines.append("*Generated by Monitoring Agent*")
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "period_hours": self.period_hours,
            "total_tasks_created": self.total_tasks_created,
            "total_tasks_completed": self.total_tasks_completed,
            "success_rate": self.success_rate,
            "pending_tasks": self.pending_tasks,
            "failed_tasks": self.failed_tasks,
            "dead_lettered_tasks": self.dead_lettered_tasks,
            "agent_stats": self.agent_stats,
            "recent_tasks_count": len(self.recent_tasks),
            "raw_data": self.raw_data,
        }


async def get_task_statistics(
    since: datetime,
    session: AsyncSession,
) -> dict[str, Any]:
    """Query task statistics from database."""
    # Get all tasks created since the cutoff
    stmt = select(Task).where(Task.created_at >= since).order_by(Task.created_at.desc())
    result = await session.execute(stmt)
    tasks = result.scalars().all()

    total_created = len(tasks)

    # Count by status
    pending = sum(1 for t in tasks if t.status == TaskStatusDB.pending)
    processing = sum(1 for t in tasks if t.status == TaskStatusDB.processing)
    completed = sum(1 for t in tasks if t.status == TaskStatusDB.completed)
    failed = sum(1 for t in tasks if t.status == TaskStatusDB.failed)
    dead_lettered = sum(1 for t in tasks if t.status == TaskStatusDB.dead_lettered)

    total_completed = completed + failed + dead_lettered
    success_rate = (completed / total_completed) if total_completed > 0 else 0.0

    # Build recent tasks list (limited)
    recent = []
    for task in tasks[:20]:
        _task_to_dict(task)
        recent.append(
            {
                "id": str(task.id),
                "action": task.action,
                "domain": task.domain,
                "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                "created_at": task.created_at.isoformat() if task.created_at else "",
                "updated_at": task.updated_at.isoformat() if task.updated_at else "",
            }
        )

    return {
        "total_created": total_created,
        "total_completed": total_completed,
        "pending": pending,
        "processing": processing,
        "completed": completed,
        "failed": failed,
        "dead_lettered": dead_lettered,
        "success_rate": success_rate,
        "recent_tasks": recent,
    }


async def get_agent_stats() -> dict[str, Any]:
    """Get agent execution statistics from container (placeholder)."""
    # In production, this would query agent_runs table or use container stats
    # For now, return empty stats
    return {}


async def generate_daily_report(
    hours: int = 24,
    session_factory=None,
) -> DailyReport:
    """Generate daily progress report.

    Args:
        hours: Lookback period in hours (default 24).
        session_factory: Optional session factory (uses default if None).

    Returns:
        DailyReport with aggregated data.
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    date_str = since.strftime("%Y-%m-%d")
    generated_at = now.isoformat()

    report = DailyReport(
        date=date_str,
        generated_at=generated_at,
        period_hours=hours,
    )

    try:
        # Get session
        if session_factory is None:
            s = get_settings()
            session_factory = get_session_factory(s)

        async with session_factory() as session:
            stats = await get_task_statistics(since, session)
            report.raw_data = stats

            report.total_tasks_created = stats["total_created"]
            report.total_tasks_completed = stats["total_completed"]
            report.success_rate = stats["success_rate"]
            report.pending_tasks = stats["pending"]
            report.failed_tasks = stats["failed"]
            report.dead_lettered_tasks = stats["dead_lettered"]
            report.recent_tasks = stats["recent_tasks"]

        # Get agent stats
        report.agent_stats = await get_agent_stats()

        # Get real system activity: RAG cache, LLM cost ledger, health
        try:
            _rag = await get_rag_stats()
            report.rag_facts = _rag["total"]
            report.rag_recent = _rag["recent"]
        except Exception as e:  # pragma: no cover
            logger.warning("RAG stats failed: %s", e)

        try:
            _cost = get_llm_cost_summary()
            report.llm_ledger_present = _cost["present"]
            report.llm_calls = _cost["calls"]
            report.llm_cache_hits = _cost["cache_hits"]
            report.llm_est_cost_usd = _cost["est_cost_usd"]
        except Exception as e:  # pragma: no cover
            logger.warning("LLM cost summary failed: %s", e)

        try:
            _health = await get_health_snapshot()
            report.health_overall = _health["overall"]
            report.health_components = _health["components"]
        except Exception as e:  # pragma: no cover
            logger.warning("Health snapshot failed: %s", e)

    except Exception as e:
        logger.error(f"Error generating daily report: {e}")
        # Report with error info
        report.raw_data["error"] = str(e)

    return report


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


async def main() -> None:
    """Generate daily report and print to stdout."""
    import json

    report = await generate_daily_report()
    print(report.to_markdown())
    print("\n--- JSON ---")
    print(json.dumps(report.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
