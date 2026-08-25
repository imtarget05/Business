"""Shared FastAPI dependencies for API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from packages.config.settings import get_settings
from packages.database.models import Organization
from packages.database.session import get_session


async def _resolve_org(requested: UUID | None, db: AsyncSession) -> UUID:
    """Resolve organization_id from request or fall back to default org."""
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
    """FastAPI dependency: resolve organization_id from request or fall back to default org."""
    return await _resolve_org(organization_id, db)