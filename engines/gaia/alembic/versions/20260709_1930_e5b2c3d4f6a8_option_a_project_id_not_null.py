"""option A migration 3c: project_id NOT NULL + cleanup

Revision ID: e5b2c3d4f6a8
Revises: d4a1b2c3e5f7
Create Date: 2026-07-09 19:30:00.000000+00:00

ADR-016 §0.5 option A migration (step 3c of 3): make ``project_id`` NOT NULL
on all definition-class resources (ObjectType/ActionType/LinkType/InterfaceType/
SharedProperty). After 3a (backfill) every row has a value; after 3b the
creation paths always set it. This step enforces the constraint at the DB
level and completes the option A migration.

Post-migration, the AuthorizationService's option B fallback branch in
resolve_resource_ownership is dead code (project_id is never NULL) — but it
is retained as a defensive guard. A separate code cleanup can remove it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2c3d4f6a8"
down_revision: str | Sequence[str] | None = "d4a1b2c3e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make project_id NOT NULL on definition-class tables.

    3a already backfilled all NULLs, so this is safe. The ALTER COLUMN
    enforces the constraint at the DB level — future inserts that omit
    project_id will be rejected (defense in depth, even if the app has a bug).
    Also update the FK ondelete from SET NULL to CASCADE (a definition-class
    resource should not survive its Project being deleted).
    """
    definition_tables = [
        "object_types",
        "action_types",
        "link_types",
        "interface_types",
    ]
    for table in definition_tables:
        # Drop the old FK (ondelete=SET NULL) and recreate with CASCADE.
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_project_id",
            table,
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.alter_column(
            table,
            "project_id",
            existing_type=sa.String(32),
            nullable=False,
        )
    # shared_properties: same FK update + ensure NOT NULL.
    op.drop_constraint("fk_shared_properties_project_id", "shared_properties", type_="foreignkey")
    op.create_foreign_key(
        "fk_shared_properties_project_id",
        "shared_properties",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "shared_properties",
        "project_id",
        existing_type=sa.String(32),
        nullable=False,
    )


def downgrade() -> None:
    """Revert: make project_id nullable + FK back to SET NULL."""
    definition_tables = [
        "object_types",
        "action_types",
        "link_types",
        "interface_types",
        "shared_properties",
    ]
    for table in definition_tables:
        op.alter_column(
            table,
            "project_id",
            existing_type=sa.String(32),
            nullable=True,
        )
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_project_id",
            table,
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
