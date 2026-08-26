"""Shared FastAPI dependencies for API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import Environment, get_settings
from packages.core.errors import AuthenticationError
from packages.database.models import Organization
from packages.database.repositories.api_keys import ApiKeyRepository
from packages.database.session import get_session

# Stable org id used ONLY by the local dev escape hatch when no DB is
# reachable (e.g. CI without persistence). Never used when tenant keys exist.
_LOCAL_DEV_ORG = UUID("00000000-0000-0000-0000-000000000001")

# engine id -> resolved org (local escape-hatch memoization)
_ESCAPE_HATCH_ORG: dict[int, UUID] = {}


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
      1. Verify the caller's X-API-Key against the DB hash table (production path).
         - If a matching active key is found, return its organization_id.
         - If key is missing -> AuthenticationError.
         - If key supplied but not found/inactive in DB:
           - In LOCAL: fall back to legacy tenant_api_keys dict.
           - In non-LOCAL: AuthenticationError.
      2. Legacy tenant_api_keys dict (local escape hatch only).
      3. Dev escape hatch (ONLY when environment == local AND no tenant keys
         configured): fall back to the default-org behavior.
      4. Otherwise: fail closed with AuthenticationError.
    """
    settings = get_settings()
    supplied = request.headers.get("X-API-Key")

    # DEBUG
    import logging
    logging.getLogger("api").warning(f"DEBUG current_org: supplied={supplied}, tenant_api_keys={settings.tenant_api_keys}, env={settings.environment}")

    if not supplied:
        # No key supplied at all
        if settings.tenant_api_keys and settings.environment is Environment.LOCAL:
            raise AuthenticationError("Missing tenant API key")
        if settings.environment is Environment.LOCAL:
            # Dev escape hatch
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

    # Key was supplied - try DB verification first
    repo = ApiKeyRepository(db)
    try:
        org_id = await repo.verify(supplied)
        logging.getLogger("api").warning(f"DEBUG current_org: repo.verify returned org_id={org_id}")
    except Exception as e:
        logging.getLogger("api").warning(f"DEBUG current_org: repo.verify raised {type(e).__name__}: {e}")
        raise
    if org_id is not None:
        logging.getLogger("api").warning(f"DEBUG current_org: DB verified org_id={org_id}")
        return org_id

    # DB verification failed - fall back to legacy keys ONLY in local
    if settings.tenant_api_keys and settings.environment == Environment.LOCAL:
        if supplied in settings.tenant_api_keys:
            raw = settings.tenant_api_keys[supplied]
            try:
                org_uuid = UUID(str(raw))
                logging.getLogger("api").warning(f"DEBUG current_org: tenant fallback org_id={org_uuid}")
                return org_uuid
            except ValueError as exc:
                raise AuthenticationError(
                    "Tenant API key maps to an invalid organization id"
                ) from exc
        raise AuthenticationError("Missing or unknown tenant API key")

    # Non-local environments: fail closed if DB key not found
    raise AuthenticationError("Missing or invalid API key")