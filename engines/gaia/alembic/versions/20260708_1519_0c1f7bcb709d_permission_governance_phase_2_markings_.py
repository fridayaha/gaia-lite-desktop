"""permission governance phase 2: markings (MAC)

Revision ID: 0c1f7bcb709d
Revises: d76884d4c7b7
Create Date: 2026-07-08 15:19:05.973700+00:00

ADR-016/017 Phase 2: Marking MAC layer.

Creates:
  - ``marking_categories``: classification axes (DataSensitivity / DataType /
    BusinessPartition). ``is_system=True`` for the Organization-derived
    subject-isolation category.
  - ``markings``: classification values (机密 / PII / 华东). System markings
    (``is_system=True``, ``source_organization_id`` set) are auto-derived
    from Organizations and cannot be manually removed.
  - ``marking_grants``: grants a marking to a Group (组授权铁律). Created by
    MARKING_ADMIN (separation of duties — not by PROJECT_OWNER).
  - ``marking_assignments``: applies a marking to a resource (polymorphic
    resource_type + resource_id). Created by PROJECT_OWNER/EDITOR using an
    existing marking (separation of duties — not by MARKING_ADMIN).

The Organization↔Marking linkage (system marking derivation) is seeded by
the application lifespan bootstrap (it needs to read the Organization row
and create the paired category+marking, which is Service logic). The
migration only creates the schema.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0c1f7bcb709d"
down_revision: str | Sequence[str] | None = "d76884d4c7b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # marking_categories: classification axes.
    op.create_table(
        "marking_categories",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_marking_categories_name"), "marking_categories", ["name"], unique=True)

    # markings: classification values (belong to a category, optionally
    # derived from an Organization for subject isolation).
    op.create_table(
        "markings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("category_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_organization_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["marking_categories.id"], ondelete="CASCADE",
                                name="fk_markings_category_id"),
        sa.ForeignKeyConstraint(["source_organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name="fk_markings_source_organization_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_markings_category_name"),
    )
    op.create_index(op.f("ix_markings_category_id"), "markings", ["category_id"], unique=False)

    # marking_grants: Group ↔ Marking (组授权铁律, MARKING_ADMIN grants).
    op.create_table(
        "marking_grants",
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("marking_id", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE",
                                name="fk_marking_grants_group_id"),
        sa.ForeignKeyConstraint(["marking_id"], ["markings.id"], ondelete="CASCADE",
                                name="fk_marking_grants_marking_id"),
        sa.PrimaryKeyConstraint("group_id", "marking_id"),
    )

    # marking_assignments: polymorphic resource marking (PROJECT_OWNER applies).
    op.create_table(
        "marking_assignments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False),
        sa.Column("resource_id", sa.String(length=32), nullable=False),
        sa.Column("marking_id", sa.String(length=32), nullable=False),
        sa.Column("is_directly_applied", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["marking_id"], ["markings.id"], ondelete="CASCADE",
                                name="fk_marking_assignments_marking_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_type", "resource_id", "marking_id",
                            name="uq_marking_assignments_resource_marking"),
    )
    op.create_index(op.f("ix_marking_assignments_resource_type"), "marking_assignments",
                    ["resource_type"], unique=False)
    op.create_index(op.f("ix_marking_assignments_resource_id"), "marking_assignments",
                    ["resource_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_marking_assignments_resource_id"), table_name="marking_assignments")
    op.drop_index(op.f("ix_marking_assignments_resource_type"), table_name="marking_assignments")
    op.drop_table("marking_assignments")
    op.drop_table("marking_grants")
    op.drop_index(op.f("ix_markings_category_id"), table_name="markings")
    op.drop_table("markings")
    op.drop_index(op.f("ix_marking_categories_name"), table_name="marking_categories")
    op.drop_table("marking_categories")
