"""permission governance phase 1: roles + role_assignments (RBAC)

Revision ID: d76884d4c7b7
Revises: 15cf0be21f7a
Create Date: 2026-07-08 14:58:33.584964+00:00

ADR-016/017 Phase 1: RBAC role layer.

Creates:
  - ``roles``: role definitions (name, scope_type, permissions JSONB).
    Nine builtin roles seeded by the application lifespan bootstrap
    (PLATFORM_ADMIN / AUDIT_ADMIN / MARKING_ADMIN / SPACE_OWNER / ... /
    OWNER / EDITOR / VIEWER / DISCOVERER).
  - ``role_assignments``: grants a Role to a Principal (typically a Group,
    per the 组授权铁律) at a scope (GLOBAL/SPACE/PROJECT). ``expires_at``
    supports JIT temporary permissions (Phase 4).

The builtin roles are NOT seeded in this migration — they're seeded by the
lifespan ``bootstrap_default_containers`` (it needs to construct the
RoleModel rows with the permission lists from ``core/permission_roles.py``,
which is application logic, not raw DDL). The migration only creates the
schema; the bootstrap is idempotent and fills the rows on every startup.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d76884d4c7b7"
down_revision: str | Sequence[str] | None = "15cf0be21f7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column(
            "permissions",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=32), nullable=False),
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE",
                                name="fk_role_assignments_role_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_id", "role_id", "scope_type", "scope_id",
                            name="uq_role_assignments_principal_role_scope"),
    )
    op.create_index(op.f("ix_role_assignments_principal_id"), "role_assignments", ["principal_id"], unique=False)
    op.create_index(op.f("ix_role_assignments_scope_id"), "role_assignments", ["scope_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_role_assignments_scope_id"), table_name="role_assignments")
    op.drop_index(op.f("ix_role_assignments_principal_id"), table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")
