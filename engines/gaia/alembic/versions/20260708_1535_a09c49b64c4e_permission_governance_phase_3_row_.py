"""permission governance phase 3: row/column security policies (ABAC)

Revision ID: a09c49b64c4e
Revises: 0c1f7bcb709d
Create Date: 2026-07-08 15:35:46.778545+00:00

ADR-016/017 Phase 3: ABAC row/column-level security.

Creates:
  - ``row_security_policies``: ObjectType-level row filter. The ``expression``
    is a Cedar policy condition referencing ``principal.attributes``. At query
    time, Cedar ``is_authorized_partial`` produces a residual (principal
    evaluated, resource unknown) → translated to a SQL WHERE predicate →
    injected via SqlGlot AST into the query (design §4, ADR-017 D4).
  - ``property_masking_policies``: Property-level column mask. When the Cedar
    condition evaluates false for the principal, the property is returned as
    null (masked). Combined with row policy = cell-level visibility.

Both use Cedar (not simpleeval) as the expression engine — non-Turing-complete,
type-safe, with partial evaluation for SQL pushdown (ADR-017 D1).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a09c49b64c4e"
down_revision: str | Sequence[str] | None = "0c1f7bcb709d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "row_security_policies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("object_type_id", sa.String(length=32), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["object_type_id"], ["object_types.id"], ondelete="CASCADE",
                                name="fk_row_security_policies_object_type_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_type_id", name="uq_row_security_policies_object_type"),
    )
    op.create_index(op.f("ix_row_security_policies_object_type_id"), "row_security_policies",
                    ["object_type_id"], unique=False)

    op.create_table(
        "property_masking_policies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("property_id", sa.String(length=32), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE",
                                name="fk_property_masking_policies_property_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("property_id", name="uq_property_masking_policies_property"),
    )
    op.create_index(op.f("ix_property_masking_policies_property_id"), "property_masking_policies",
                    ["property_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_property_masking_policies_property_id"), table_name="property_masking_policies")
    op.drop_table("property_masking_policies")
    op.drop_index(op.f("ix_row_security_policies_object_type_id"), table_name="row_security_policies")
    op.drop_table("row_security_policies")
