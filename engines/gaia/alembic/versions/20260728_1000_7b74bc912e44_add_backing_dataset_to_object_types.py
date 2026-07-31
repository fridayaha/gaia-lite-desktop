"""add backing_dataset_api_name to object_types (Palantir "backing datasource")

Revision ID: 7b74bc912e44
Revises: dc22d664a7f9
Create Date: 2026-07-28 10:00:00.000000+00:00

Adds an ObjectType-level ``backing_dataset_api_name`` column, aligning with
Palantir Foundry's "backing datasource" concept (see docs/design/
dataset-ontology-binding.md §2.3 and the Palantir create-object-type docs).

Semantics:
* Convenience reference for the OT's default / main backing dataset — used by
  list badges, detail pages, and (future) permission anchoring.
* Nullable: None when unbound, or for pure MDO types with no clear primary.
* NOT the authoritative physical binding — that remains per-property
  ``properties.backing_dataset_api_name`` (supports column-wise MDO: one OT,
  multiple datasets). This OT-level field is a stable default reference only.
* Populated by ``OntologyService.link_dataset`` on first bind; retained across
  subsequent re-binds (first bound = primary source).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b74bc912e44"
down_revision: str | Sequence[str] | None = "dc22d664a7f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable column — existing rows stay NULL (unbound). Populated lazily by
    # link_dataset on first bind; no backfill needed (property-level mapping
    # remains the source of truth, so NULL here is semantically valid).
    op.add_column(
        "object_types",
        sa.Column(
            "backing_dataset_api_name",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("object_types", "backing_dataset_api_name")
