# -*- coding: utf-8 -*-
"""Health check module for monitoring agent.

Checks system components:
- API service availability (HTTP /health endpoint)
- Database connectivity
- Agent registry health (registered agents, LLM provider)
- Task queue status
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from packages.core.bootstrap import get_container
from packages.database.session import check_database
from packages.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ComponentCheck:
    """Result of a single component health check."""
    name: str
    status: str = "ok"  # ok, warning, error, unavailable
    message: str = ""
    response_time_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthCheckResult:
    """Aggregated health check result."""
    timestamp: str = ""
    overall: str = "ok"  # ok, degraded, down
    checks: list[ComponentCheck] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "response_time_ms": c.response_time_ms,
                    "details": c.details,
                }
                for c in self.checks
            ],
            "summary": self.summary,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# 🏥 Health Check Report",
            "",
            f"**Overall**: {self.overall.upper()}",
            f"*Generated: {self.timestamp}*",
            "",
            "## Components",
            "",
        ]
        for c in self.checks:
            icon = {"ok": "✅", "warning": "⚠️", "error": "🚨", "unavailable": "❓"}[c.status]
            lines.append(f"- {icon} **{c.name}**: {c.message}")
            if c.response_time_ms:
                lines.append(f"  - Response time: {c.response_time_ms:.1f}ms")
            if c.details:
                for k, v in c.details.items():
                    lines.append(f"  - {k}: {v}")
        lines.append("")
        return "\n".join(lines)


async def check_api(base_url: str = "http://localhost:8000") -> ComponentCheck:
    """Check API service health endpoint."""
    check = ComponentCheck(name="api", status="unavailable")
    start = datetime.now(timezone.utc)
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/health")
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            check.response_time_ms = elapsed
            
            if resp.status_code == 200:
                data = resp.json()
                check.status = "ok"
                check.message = "API service is healthy"
                check.details = {
                    "service": data.get("service", "unknown"),
                    "endpoint": "/health",
                }
            else:
                check.status = "error"
                check.message = f"API returned status {resp.status_code}"
    except httpx.ConnectError:
        check.status = "error"
        check.message = "Cannot connect to API service"
    except Exception as e:
        check.status = "error"
        check.message = f"API check failed: {str(e)}"
    
    return check


async def check_database_health() -> ComponentCheck:
    """Check database connectivity using existing check_database function."""
    check = ComponentCheck(name="database", status="unavailable")
    
    try:
        db_ok = await check_database()
        if db_ok:
            check.status = "ok"
            check.message = "Database is reachable"
        else:
            check.status = "error"
            check.message = "Database connectivity check failed"
    except Exception as e:
        check.status = "error"
        check.message = f"Database check error: {str(e)}"
    
    return check


async def check_agent_registry() -> ComponentCheck:
    """Check agent registry health from container."""
    check = ComponentCheck(name="agent_registry", status="unavailable")
    
    try:
        container = get_container()
        agent_count = len(container.registry.agents)
        llm_provider = container.settings.llm_provider.value
        
        check.status = "ok"
        check.message = f"{agent_count} agents registered"
        check.details = {
            "agent_count": agent_count,
            "llm_provider": llm_provider,
        }
        
        if agent_count == 0:
            check.status = "warning"
            check.message = "No agents registered in registry"
    except Exception as e:
        check.status = "error"
        check.message = f"Agent registry check error: {str(e)}"
    
    return check


async def check_task_queue() -> ComponentCheck:
    """Check pending task queue status (placeholder)."""
    check = ComponentCheck(name="task_queue", status="unavailable")
    check.message = "Task queue monitoring not yet implemented"
    return check


async def run_health_check(
    api_base_url: str = "http://localhost:8000",
) -> HealthCheckResult:
    """Run all health checks and return aggregated result.

    Wrapped in a tracing span (Phase E) — the tracer is a no-op unless a backend
    is configured via TRACING_BACKEND, so this never adds overhead or failures.
    """
    from packages.core.tracing import get_tracer

    tracer = get_tracer()
    with tracer.span("health_check") as _sid:
        timestamp = datetime.now(timezone.utc).isoformat()
        result = HealthCheckResult(timestamp=timestamp)

        # Run checks concurrently
        checks = await asyncio.gather(
            check_api(api_base_url),
            check_database_health(),
            check_agent_registry(),
            check_task_queue(),
            return_exceptions=True,
        )

        result.checks = []
        for item in checks:
            if isinstance(item, Exception):
                result.checks.append(ComponentCheck(
                    name="unknown",
                    status="error",
                    message=f"Check failed: {str(item)}",
                ))
            else:
                result.checks.append(item)

        # Determine overall status
        statuses = [c.status for c in result.checks]
        if "error" in statuses:
            result.overall = "down"
        elif "warning" in statuses:
            result.overall = "degraded"
        else:
            result.overall = "ok"

        # Summary
        result.summary = {
            "total_checks": len(result.checks),
            "ok_count": sum(1 for c in result.checks if c.status == "ok"),
            "warning_count": sum(1 for c in result.checks if c.status == "warning"),
            "error_count": sum(1 for c in result.checks if c.status == "error"),
            "unavailable_count": sum(1 for c in result.checks if c.status == "unavailable"),
        }

        tracer.event("health_check_completed", overall=result.overall)

    return result


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def main() -> None:
    """CLI entry point for health check."""
    import json
    
    api_url = "http://localhost:8000"  # default
    result = await run_health_check(api_url)
    
    print(result.to_markdown())
    print("\n--- JSON ---")
    print(json.dumps(result.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
