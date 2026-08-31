#!/usr/bin/env python
"""Seed API keys for all organizations (Task 5.3).

Usage:
    python scripts/seed_api_key.py              # Create one key per org
    python scripts/seed_api_key.py --org <id>   # Create key for specific org
    python scripts/seed_api_key.py --list       # List existing keys

The plaintext key is printed ONCE at creation time. Store it securely.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from packages.config.settings import get_settings
from packages.database.repositories.api_keys import ApiKeyRepository
from packages.database.session import get_session_factory, session_scope


async def create_key_for_org(org_id: UUID, name: str) -> tuple[str, str]:
    """Create an API key for an organization. Returns (key_id, plaintext_key)."""
    async with session_scope() as session:
        repo = ApiKeyRepository(session)
        api_key, plaintext = await repo.create_key(org_id, name)
        return str(api_key.id), plaintext


async def list_keys_for_org(org_id: UUID) -> list[tuple[str, str, bool, str]]:
    """List API keys for an organization. Returns list of (key_id, name, is_active, created_at)."""
    async with session_scope() as session:
        repo = ApiKeyRepository(session)
        keys = await repo.list_keys(org_id)
        return [(str(k.id), k.name, k.is_active, k.created_at.isoformat()) for k in keys]


async def list_all_orgs() -> list[tuple[str, str]]:
    """List all organizations. Returns list of (org_id, name)."""
    from sqlalchemy import select

    from packages.database.models import Organization

    async with session_scope() as session:
        result = await session.execute(select(Organization).order_by(Organization.name))
        orgs = result.scalars().all()
        return [(str(o.id), o.name) for o in orgs]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed API keys for organizations")
    parser.add_argument(
        "--org", type=str, help="Organization ID to create key for (default: all orgs)"
    )
    parser.add_argument("--name", type=str, default="default", help="Key name (default: 'default')")
    parser.add_argument(
        "--list", action="store_true", help="List existing keys instead of creating"
    )
    args = parser.parse_args()

    # Initialize DB connection
    settings = get_settings()
    get_session_factory(settings)

    try:
        if args.list:
            orgs = await list_all_orgs()
            if not orgs:
                print("No organizations found.")
                return 0

            for org_id, org_name in orgs:
                keys = await list_keys_for_org(UUID(org_id))
                print(f"\nOrganization: {org_name} ({org_id})")
                if not keys:
                    print("  No API keys")
                else:
                    for key_id, name, is_active, created_at in keys:
                        status = "active" if is_active else "revoked"
                        print(f"  {key_id}  [{status}]  {name}  (created: {created_at})")
            return 0

        if args.org:
            org_ids = [UUID(args.org)]
        else:
            orgs = await list_all_orgs()
            if not orgs:
                print("No organizations found. Create one first.")
                return 1
            org_ids = [UUID(oid) for oid, _ in orgs]

        for org_id in org_ids:
            key_id, plaintext = await create_key_for_org(org_id, args.name)
            print(f"Organization: {org_id}")
            print(f"  Key ID:     {key_id}")
            print(f"  Key Name:   {args.name}")
            print(f"  Plaintext:  {plaintext}")
            print()
            print("  ⚠️  Store the plaintext key securely. It will NOT be shown again.")
            print()

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        from packages.database.session import dispose_engine

        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
