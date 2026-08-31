"""API Key repository — DB-backed authentication (Task 5.3).

All operations are organization-scoped. The plaintext key is returned only once
at creation; verification uses constant-time hash comparison.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import ApiKey


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_key(self, organization_id: UUID, name: str) -> tuple[ApiKey, str]:
        """Create a new API key for an organization.

        Returns the ApiKey model and the PLAINTEXT key (only time it's visible).
        The stored key_hash is SHA-256 hex digest.
        """
        # Generate a secure random key: "boas_" prefix + 32 bytes = 43 chars base64url
        # Total ~50 chars, plenty of entropy.
        plaintext = "boas_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

        api_key = ApiKey(
            key_hash=key_hash,
            organization_id=organization_id,
            name=name,
            is_active=True,
        )
        self._session.add(api_key)
        await self._session.flush()
        return api_key, plaintext

    async def verify(self, plaintext_key: str) -> UUID | None:
        """Verify a plaintext API key against the DB hash table.

        Returns the organization_id if valid and active, else None.
        Updates last_used_at on successful verification.
        """
        key_hash = hashlib.sha256(plaintext_key.encode()).hexdigest()

        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        result = await self._session.execute(stmt)
        api_key = result.scalars().first()

        if api_key is None or not api_key.is_active:
            return None

        # Update last_used_at
        from datetime import datetime

        api_key.last_used_at = datetime.now(UTC)
        await self._session.flush()

        return api_key.organization_id

    async def get_key(self, organization_id: UUID, key_id: UUID) -> ApiKey | None:
        """Fetch a specific API key by ID (org-scoped)."""
        stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == organization_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_keys(self, organization_id: UUID) -> list[ApiKey]:
        """List all API keys for an organization (hashes never exposed)."""
        stmt = (
            select(ApiKey)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def revoke_key(self, organization_id: UUID, key_id: UUID) -> bool:
        """Revoke (deactivate) an API key. Returns True if key was found and revoked."""
        api_key = await self.get_key(organization_id, key_id)
        if api_key is None:
            return False
        api_key.is_active = False
        await self._session.flush()
        return True

    async def rename_key(self, organization_id: UUID, key_id: UUID, name: str) -> ApiKey | None:
        """Rename an API key. Returns updated key or None if not found."""
        api_key = await self.get_key(organization_id, key_id)
        if api_key is None:
            return None
        api_key.name = name
        await self._session.flush()
        return api_key


__all__ = ["ApiKeyRepository"]
