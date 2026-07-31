"""permission governance phase 0: containers + identity + ownership columns

Revision ID: 15cf0be21f7a
Revises: 8b4f2c1d0e7a
Create Date: 2026-07-08 14:30:56.886207+00:00

ADR-016/017 Phase 0: establish the permission governance foundation.

Creates:
  - Group 1 (three-tier containers): organizations, spaces (1:1 Ontology),
    space_organizations (whitelist), projects.
  - Group 2 (identity): principals (polymorphic base), users, groups,
    group_memberships, service_users.
  - Ownership columns on existing models (nullable-first, backfilled by the
    application lifespan bootstrap, then NOT NULL in a later revision):
      ontologies.space_id          (1:1 Space, ondelete=RESTRICT)
      object_types.project_id      (option B reservation, ondelete=SET NULL)
      link_types.project_id        (same)
      action_types.project_id      (same)
      interface_types.project_id   (same)
      shared_properties.project_id (same)
      data_sources.project_id      (resource ownership, ondelete=SET NULL)
      datasets.project_id          (same)
      sync_tasks.project_id        (same)
      credentials.project_id       (same)

Seeds the default Organization (org-default) so single-tenant deployments
work out of the box (progressive disclosure — the default org is hidden
from the UI). The default Space + Project are bootstrapped by the
application lifespan (they need Service logic to create the paired
Ontology atomically, design §9.2).

This migration was generated via ``alembic revision --autogenerate`` and
then hand-edited to:
  - add ``server_default`` to all NOT NULL VARCHAR/Text columns with a
    Python-side ``default=`` (otherwise existing-row backfill and future
    inserts without every field fail);
  - name every foreign key explicitly (autogenerate left them as None,
    which makes downgrade ``drop_constraint`` unreliable);
  - seed the default Organization row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "15cf0be21f7a"
down_revision: str | Sequence[str] | None = "8b4f2c1d0e7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default Organization id (fixed so the lifespan bootstrap and tests can
# reference it deterministically). 32-char hex to match new_uuid() format.
_DEFAULT_ORG_ID = "00000000000000000000000000000001"


def upgrade() -> None:
    # ── Group 1: three-tier containers ──
    # Order matters for FK dependencies: organizations first (no deps),
    # then spaces (FK → ontologies, which already exists), then projects
    # (FK → spaces), then space_organizations (FK → both).

    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("api_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("org_type", sa.String(length=20), nullable=False, server_default="INTERNAL"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_organizations_api_name"), "organizations", ["api_name"], unique=True)

    # spaces: 1:1 Ontology (ontology_id unique + ondelete=RESTRICT).
    op.create_table(
        "spaces",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("api_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("ontology_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontologies.id"], ondelete="RESTRICT",
                                name="fk_spaces_ontology_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ontology_id", name="uq_spaces_ontology_id"),
    )
    op.create_index(op.f("ix_spaces_api_name"), "spaces", ["api_name"], unique=True)

    # projects: belongs to a Space (api_name unique within Space).
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("api_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("space_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE",
                                name="fk_projects_space_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "api_name", name="uq_projects_space_api_name"),
        comment="协作权限边界（权限原子单位）",
    )
    op.create_index(op.f("ix_projects_space_id"), "projects", ["space_id"], unique=False)

    # space_organizations: whitelist (cross-org collaboration channel).
    op.create_table(
        "space_organizations",
        sa.Column("space_id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name="fk_space_organizations_organization_id"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE",
                                name="fk_space_organizations_space_id"),
        sa.PrimaryKeyConstraint("space_id", "organization_id"),
    )
    op.create_index("ix_space_organizations_org", "space_organizations", ["organization_id"], unique=False)

    # ── Group 2: identity layer ──
    # principals is the polymorphic base (no FK deps). users/groups depend
    # on organizations; group_memberships depends on groups + users;
    # service_users depends on users.

    op.create_table(
        "principals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_principals_principal_type"), "principals", ["principal_type"], unique=False)

    # users: home_organization FK → organizations (SET NULL on org delete).
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("attributes", sa.JSON().with_variant(
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False, server_default="{}"),
        sa.Column("home_organization", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["home_organization"], ["organizations.id"], ondelete="SET NULL",
                                name="fk_users_home_organization"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject", name="uq_users_subject"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # groups: belongs to one org (CASCADE), self-reference for nesting.
    op.create_table(
        "groups",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("parent_group_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE",
                                name="fk_groups_organization_id"),
        sa.ForeignKeyConstraint(["parent_group_id"], ["groups.id"], ondelete="CASCADE",
                                name="fk_groups_parent_group_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_groups_org_name"),
    )
    op.create_index(op.f("ix_groups_organization_id"), "groups", ["organization_id"], unique=False)

    # group_memberships: composite PK (group_id, user_id).
    op.create_table(
        "group_memberships",
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE",
                                name="fk_group_memberships_group_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_group_memberships_user_id"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )
    op.create_index("ix_group_memberships_user", "group_memberships", ["user_id"], unique=False)

    # service_users: owner FK → users (RESTRICT — can't delete a user who
    # owns service accounts without reassigning).
    op.create_table(
        "service_users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scopes", sa.JSON().with_variant(
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False, server_default="[]"),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner"], ["users.id"], ondelete="RESTRICT",
                                name="fk_service_users_owner"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Ownership columns on existing models (nullable-first) ──
    # Design §9.1: nullable now, backfilled by lifespan bootstrap (default
    # Space/Project), then NOT NULL in a later revision. ondelete=SET NULL
    # for resources (business assets preserved on Project deletion),
    # ondelete=RESTRICT for ontologies.space_id (core asset protection).

    op.add_column("ontologies", sa.Column("space_id", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_ontologies_space_id", "ontologies", ["space_id"])
    op.create_foreign_key("fk_ontologies_space_id", "ontologies", "spaces",
                          ["space_id"], ["id"], ondelete="RESTRICT")

    for table in ("object_types", "link_types", "action_types",
                  "interface_types", "shared_properties"):
        op.add_column(table, sa.Column("project_id", sa.String(length=32), nullable=True))
        op.create_foreign_key(f"fk_{table}_project_id", table, "projects",
                              ["project_id"], ["id"], ondelete="SET NULL")

    for table in ("data_sources", "datasets", "sync_tasks", "credentials"):
        op.add_column(table, sa.Column("project_id", sa.String(length=32), nullable=True))
        op.create_index(op.f(f"ix_{table}_project_id"), table, ["project_id"], unique=False)
        op.create_foreign_key(f"fk_{table}_project_id", table, "projects",
                              ["project_id"], ["id"], ondelete="SET NULL")

    # ── Seed default Organization (single-tenant fallback) ──
    # Design §9.2: Organization is seeded in the migration (must exist
    # before FK references). The default Space + Project are bootstrapped
    # by the application lifespan (they need Service logic to create the
    # paired Ontology atomically).
    op.execute(
        f"INSERT INTO organizations (id, api_name, display_name, description, org_type, status, "
        f"created_at, updated_at) VALUES ('{_DEFAULT_ORG_ID}', 'org-default', "
        f"'Default Organization', 'Default organization for single-tenant deployments', "
        f"'INTERNAL', 'ACTIVE', NOW(), NOW()) "
        # Only insert if not already present (idempotent — safe to re-run).
        "ON CONFLICT (api_name) DO NOTHING"
    )


def downgrade() -> None:
    # Drop ownership columns + FKs (reverse order of upgrade).
    for table in ("credentials", "sync_tasks", "datasets", "data_sources"):
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_index(op.f(f"ix_{table}_project_id"), table_name=table)
        op.drop_column(table, "project_id")

    for table in ("shared_properties", "interface_types", "action_types", "link_types", "object_types"):
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_column(table, "project_id")

    op.drop_constraint("fk_ontologies_space_id", "ontologies", type_="foreignkey")
    op.drop_constraint("uq_ontologies_space_id", "ontologies", type_="unique")
    op.drop_column("ontologies", "space_id")

    # Drop identity tables (reverse FK dependency order).
    op.drop_table("service_users")
    op.drop_index("ix_group_memberships_user", table_name="group_memberships")
    op.drop_table("group_memberships")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_groups_organization_id"), table_name="groups")
    op.drop_table("groups")
    op.drop_index(op.f("ix_principals_principal_type"), table_name="principals")
    op.drop_table("principals")

    # Drop container tables.
    op.drop_index("ix_space_organizations_org", table_name="space_organizations")
    op.drop_table("space_organizations")
    op.drop_index(op.f("ix_projects_space_id"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_spaces_api_name"), table_name="spaces")
    op.drop_table("spaces")
    op.drop_index(op.f("ix_organizations_api_name"), table_name="organizations")
    # Remove the seeded default org row (only our seed, not user-created orgs).
    op.execute(f"DELETE FROM organizations WHERE id = '{_DEFAULT_ORG_ID}'")
    op.drop_table("organizations")
