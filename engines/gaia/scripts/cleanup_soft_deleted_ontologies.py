#!/usr/bin/env python3
"""v5.2 cleanup script: physically delete soft-deleted ontologies past cooldown.

Per design §七.4, soft-deleted ontologies (``deleted_at IS NOT NULL``) are
retained for ``settings.soft_delete_retention_days`` (default 7) so a
``POST /restore`` can recover them. After the cooldown, this script reaps the
PG rows (CASCADE removes children). Physical resources (Doris idx tables,
INDEX pipelines) were already dropped at soft-delete time (decision 10:
Iceberg/Dataset are never touched), so there is nothing physical to clean here
— this is a pure PG row deletion.

Usage:
    # Dry-run (default): list what would be deleted, change nothing.
    uv run python scripts/cleanup_soft_deleted_ontologies.py

    # Actually delete.
    uv run python scripts/cleanup_soft_deleted_ontologies.py --execute

Exit codes:
    0  success (or dry-run completed)
    1  error during deletion (per-ontology failures are logged and skipped)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontology.config.database import async_session_maker
from ontology.config.settings import settings
from ontology.core.models.ontology import OntologyModel


async def _list_expired(session: AsyncSession, now: datetime, retention_days: int) -> list[OntologyModel]:
    """Return soft-deleted ontologies whose cooldown window has elapsed."""

    cutoff = now - timedelta_days(retention_days)
    # deleted_at < cutoff (both timezone-aware). Use a parameterized compare.
    stmt = select(OntologyModel).where(
        OntologyModel.deleted_at.is_not(None),
        OntologyModel.deleted_at < cutoff,
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def timedelta_days(days: int) -> datetime:
    """timezone-aware now - days (kept helper to avoid importing timedelta inline)."""
    from datetime import timedelta

    return datetime.now(UTC) - timedelta(days=days)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()

    retention = settings.soft_delete_retention_days
    now = datetime.now(UTC)
    cutoff = now - timedelta_days(retention)

    print(f"Soft-delete retention: {retention} days")
    print(f"Cutoff (deleted_at < {cutoff.isoformat()})")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN (no changes)'}")
    print()

    async with async_session_maker() as session:
        expired = await _list_expired(session, now, retention)
        if not expired:
            print("No soft-deleted ontologies past the cooldown window. Nothing to do.")
            return 0

        print(f"Found {len(expired)} ontologies to reap:")
        failures = 0
        for m in expired:
            print(f"  - {m.api_name} (deleted_at={m.deleted_at})")
            if not args.execute:
                continue
            try:
                await session.delete(m)
                await session.commit()
                print(f"    deleted: {m.api_name}")
            except Exception as exc:  # noqa: BLE001 — best-effort reaper
                await session.rollback()
                failures += 1
                print(f"    FAILED: {m.api_name}: {exc}", file=sys.stderr)

        if not args.execute:
            print()
            print("Dry-run complete. Re-run with --execute to delete.")

        if failures:
            print(f"\n{failures} deletion(s) failed — see stderr above.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
