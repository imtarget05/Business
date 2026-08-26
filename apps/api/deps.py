"""Shared FastAPI dependencies for API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import Environment, get_settings
from packages.core.errors import AuthenticationError
from packages.database.models import Organization
from packages.database.session import get_session

# Stable org id used ONLY by the local dev escape hatch when no DB is
# reachable (e.g. CI without persistence). Never used when tenant keys exist.
_LOCAL_DEV_ORG = UUID("00000000-0000-0000-0000-000000000001")


async def _resolve_org(requested: UUID | None, db: AsyncSession) -> UUID:
    """Resolve organization_id from request or fall back to default org.

    DEV ESCAPE HATCH ONLY: used by :func:`current_org` when no tenant keys are
    configured and the environment is ``local``. Never trust client-supplied
    organization ids in non-local environments.
    """
    if requested is not None:
        return requested

    # Fall back to first organization in DB (dev/default behavior)
    from sqlalchemy import select

    row = (
        await db.execute(select(Organization).order_by(Organization.created_at))
    ).scalars().first()
    if row is None:
        from packages.core.errors import ValidationError

        raise ValidationError(
            "organization_id is required (no default organization exists)"
        )
    return row.id


async def resolve_org(
    organization_id: UUID | None = None,
    db: AsyncSession = Depends(get_session),
) -> UUID:
    """FastAPI dependency: legacy org resolution (local dev only)."""
    return await _resolve_org(organization_id, db)


async def current_org(request: Request, db: AsyncSession = Depends(get_session)) -> UUID:
    """Bind the caller to an organization via THEIR API key (server-side).

    Resolution order:
      1. If ``tenant_api_keys`` is configured: look up the caller's X-API-Key in
         the mapping; unknown/missing keys are rejected. The returned org is
         derived from the key alone — any client-supplied organization_id is
         ignored everywhere.
      2. Dev escape hatch (ONLY when environment == local AND no tenant keys
         configured): fall back to the default-org behavior.
      3. Otherwise: fail closed with AuthenticationError.
    """
    settings = get_settings()
    supplied = request.headers.get("X-API-Key")

    if settings.tenant_api_keys:
        if not supplied or supplied not in settings.tenant_api_keys:
            raise AuthenticationError("Missing or unknown tenant API key")
        raw = settings.tenant_api_keys[supplied]
        try:
            return UUID(str(raw))
        except ValueError as exc:
            raise AuthenticationError(
                "Tenant API key maps to an invalid organization id"
            ) from exc

    if settings.environment is Environment.LOCAL:
        # Dev escape hatch: default-org behavior, but never hard-fail (or
        # repeatedly stall) when no database is reachable — remember the
        # failure per engine and fall back to a stable local development org.
        engine_id = id(db.bind)
        cached = _ESCAPE_HATCH_ORG.get(engine_id)
        if cached is not None:
            return cached
        try:
            org = await _resolve_org(None, db)
        except Exception as exc:  # noqa: BLE001 - DB down in local dev
            from sqlalchemy.exc import OperationalError

            if isinstance(exc, OperationalError) or "connect" in str(exc).lower():
                _ESCAPE_HATCH_ORG[engine_id] = _LOCAL_DEV_ORG
                return _LOCAL_DEV_ORG
            raise
        _ESCAPE_HATCH_ORG[engine_id] = org
        return org

    raise AuthenticationError(
        "Tenant authentication not configured for this environment"
    )


# engine id -> resolved org (local escape-hatch memoization)
_ESCAPE_HATCH_ORG: dict[int, UUID] = {}
