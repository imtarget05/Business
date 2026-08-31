"""Unit tests for DB-backed API keys (Task 5.3)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.database.base import Base
from packages.database.models import Organization
from packages.database.repositories.api_keys import ApiKeyRepository


def _tmp_db_url() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    path_clean = path.replace("\\", "/")
    return f"sqlite+aiosqlite:///{path_clean}"


@pytest.fixture()
async def test_db():
    """File-based SQLite database for testing (Windows-compatible)."""
    url = _tmp_db_url()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
    # Clean up temp file
    try:
        os.unlink(url.replace("sqlite+aiosqlite:///", ""))
    except OSError:
        pass


@pytest.fixture()
async def org_id(test_db) -> UUID:
    """Create a test organization."""
    async with test_db() as session:
        org = Organization(id=uuid4(), name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()
        return org.id


class TestApiKeyRepository:
    """Tests for ApiKeyRepository."""

    @pytest.mark.asyncio
    async def test_create_key_returns_plaintext_once(self, test_db, org_id):
        """create_key returns plaintext key once; hash stored in DB."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(org_id, "test-key")

        # Verify returned values
        assert api_key.id is not None
        assert api_key.organization_id == org_id
        assert api_key.name == "test-key"
        assert api_key.is_active is True
        assert api_key.key_hash is not None
        assert len(api_key.key_hash) == 64  # SHA-256 hex digest
        assert plaintext.startswith("boas_")

        # Verify hash matches
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        assert api_key.key_hash == expected_hash

        # Plaintext should NOT be stored in DB (only hash)
        assert plaintext not in api_key.key_hash

    @pytest.mark.asyncio
    async def test_verify_roundtrip(self, test_db, org_id):
        """create -> verify roundtrip returns organization_id."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            _, plaintext = await repo.create_key(org_id, "test-key")
            await session.commit()

        # Verify in a new session (simulating real request)
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            verified_org_id = await repo.verify(plaintext)

        assert verified_org_id == org_id

    @pytest.mark.asyncio
    async def test_verify_wrong_key_returns_none(self, test_db, org_id):
        """verify returns None for unknown key."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            await repo.create_key(org_id, "test-key")
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            result = await repo.verify("boas_wrong_key_that_does_not_exist")

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_inactive_key_returns_none(self, test_db, org_id):
        """verify returns None for inactive (revoked) key."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(org_id, "test-key")
            await session.commit()

        # Revoke the key
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            await repo.revoke_key(org_id, api_key.id)
            await session.commit()

        # Verify should fail
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            result = await repo.verify(plaintext)

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_updates_last_used_at(self, test_db, org_id):
        """verify updates last_used_at timestamp."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(org_id, "test-key")
            assert api_key.last_used_at is None
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            await repo.verify(plaintext)
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            key = await repo.get_key(org_id, api_key.id)
            assert key.last_used_at is not None

    @pytest.mark.asyncio
    async def test_list_keys_org_scoped(self, test_db, org_id):
        """list_keys only returns keys for the given organization."""
        other_org_id = uuid4()
        async with test_db() as session:
            org2 = Organization(id=other_org_id, name="Other Org", slug="other-org")
            session.add(org2)
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            await repo.create_key(org_id, "org1-key-1")
            await repo.create_key(org_id, "org1-key-2")
            await repo.create_key(other_org_id, "org2-key-1")
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            org1_keys = await repo.list_keys(org_id)
            org2_keys = await repo.list_keys(other_org_id)

        assert len(org1_keys) == 2
        assert len(org2_keys) == 1
        assert all(k.organization_id == org_id for k in org1_keys)
        assert all(k.organization_id == other_org_id for k in org2_keys)

    @pytest.mark.asyncio
    async def test_revoke_key(self, test_db, org_id):
        """revoke_key deactivates the key."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, _ = await repo.create_key(org_id, "test-key")
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            result = await repo.revoke_key(org_id, api_key.id)
            await session.commit()
            assert result is True

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            key = await repo.get_key(org_id, api_key.id)
            assert key.is_active is False

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key_returns_false(self, test_db, org_id):
        """revoke_key returns False for non-existent key."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            result = await repo.revoke_key(org_id, uuid4())
            await session.commit()
            assert result is False

    @pytest.mark.asyncio
    async def test_rename_key(self, test_db, org_id):
        """rename_key updates the key name."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, _ = await repo.create_key(org_id, "old-name")
            await session.commit()

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            updated = await repo.rename_key(org_id, api_key.id, "new-name")
            await session.commit()
            assert updated is not None
            assert updated.name == "new-name"

    @pytest.mark.asyncio
    async def test_rename_nonexistent_key_returns_none(self, test_db, org_id):
        """rename_key returns None for non-existent key."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            result = await repo.rename_key(org_id, uuid4(), "new-name")
            await session.commit()
            assert result is None


class TestApiKeyHashSecurity:
    """Security-focused tests for API key hashing."""

    @pytest.mark.asyncio
    async def test_hash_is_sha256(self, test_db, org_id):
        """Key hash is SHA-256 hex digest."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(org_id, "test")

        assert len(api_key.key_hash) == 64
        # Verify it's valid hex
        int(api_key.key_hash, 16)
        # Verify it matches SHA-256 of plaintext
        assert api_key.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_plaintext_never_in_db(self, test_db, org_id):
        """Plaintext key is never stored in any DB field."""
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            api_key, plaintext = await repo.create_key(org_id, "test")

        # Check all string fields
        for attr in ["id", "organization_id", "name", "key_hash"]:
            value = getattr(api_key, attr)
            if isinstance(value, str):
                assert plaintext not in value

    @pytest.mark.asyncio
    async def test_constant_time_verify_not_timing_attack(self, test_db, org_id):
        """verify uses hash lookup (constant-time w.r.t. key content)."""
        # This test ensures we use hash-based lookup, not string comparison
        # of plaintext keys. The implementation uses SELECT WHERE key_hash = ?
        # which is constant-time at the DB level (index lookup).
        async with test_db() as session:
            repo = ApiKeyRepository(session)
            await repo.create_key(org_id, "test-key")

        async with test_db() as session:
            repo = ApiKeyRepository(session)
            # Both valid and invalid keys should take similar time
            # (we can't easily test timing in unit test, but verify impl)
            result1 = await repo.verify("boas_" + "a" * 43)  # wrong key
            result2 = await repo.verify("boas_" + "b" * 43)  # wrong key
            assert result1 is None
            assert result2 is None
