"""Health / readiness endpoints (STEP 0.11).

/health — process liveness only, never touches DB or LLM.
/ready  — database connectivity + critical dependency checks (LLM is NOT called).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from packages.core.bootstrap import get_container
from packages.database.session import check_database

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "business-ops-api"}


@router.get("/ready")
async def ready() -> JSONResponse:
    container = get_container()
    db_ok = await check_database()
    llm_provider = container.settings.llm_provider.value
    body = {
        "status": "ready" if db_ok else "not_ready",
        "checks": {
            "database": "ok" if db_ok else "unavailable",
            # Provider selection is config-level; we never call the LLM here.
            "llm_provider": llm_provider,
        },
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)
