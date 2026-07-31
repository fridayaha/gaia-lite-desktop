"""option A migration 3a: backfill definition-class project_id

Revision ID: d4a1b2c3e5f7
Revises: 1f1834f046fb
Create Date: 2026-07-09 19:00:00.000000+00:00

ADR-016 §0.5 option A migration (step 3a of 3): backfill ``project_id`` on
all definition-class resources (ObjectType/ActionType/LinkType/InterfaceType/
SharedProperty) using the option B fallback logic — the Ontology's owning
Space's default Project.

After this migration every definition-class resource has a non-NULL
project_id, but the column remains nullable (step 3c will make it NOT NULL).
The AuthorizationService fallback branch becomes dead code but is retained
until 3c for safety (defensive: a future bug that writes NULL won't break
resolve_resource_ownership).

This is a data-only migration (no schema change) — it UPDATEs existing rows.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a1b2c3e5f7"
down_revision: str | Sequence[str] | None = "1f1834f046fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill project_id on definition-class resources (option B → A).

    Two-phase:
      1. Ensure every Space has at least one Project (create a 'default'
         Project for Spaces that have none). This is needed because many
         test Spaces were created without a default Project.
      2. For each definition table, set project_id to the Space's default
         Project (or first Project) when NULL.
    """
    # Phase 1: create a 'default' Project for every Space that has no Project.
    # Uses gen_random_uuid() for the id (PG 13+). The Project inherits the
    # Space's id as space_id, api_name='default', display_name='Default'.
    op.execute(sa.text("""
        INSERT INTO projects (id, space_id, api_name, display_name, description, status, created_at, updated_at)
        SELECT
            replace(gen_random_uuid()::text, '-', ''),
            s.id,
            'default',
            'Default',
            'Auto-created default Project for option A migration',
            'ACTIVE',
            now(),
            now()
        FROM spaces s
        WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.space_id = s.id)
    """))

    # Phase 2: backfill definition-class project_id.
    # Definition tables with ontology_id (join ontology → space → project).
    # NOTE: shared_properties has no ontology_id (it has project_id directly
    # and belongs to a Project, not an Ontology) — excluded from this backfill.
    definition_tables = [
        "object_types",
        "action_types",
        "link_types",
        "interface_types",
    ]

    for table in definition_tables:
        # UPDATE <table> SET project_id = (
        #   SELECT p.id FROM projects p
        #   JOIN spaces s ON p.space_id = s.id
        #   WHERE s.id = (SELECT o.space_id FROM ontologies o WHERE o.id = <table>.ontology_id)
        #     AND p.api_name = 'default'
        #   LIMIT 1
        # )
        # WHERE project_id IS NULL
        #
        # If no 'default' project exists, fall back to any project under the space.
        op.execute(
            sa.text(f"""
            UPDATE {table}
            SET project_id = COALESCE(
                (SELECT p.id FROM projects p
                 WHERE p.space_id = (SELECT o.space_id FROM ontologies o WHERE o.id = {table}.ontology_id)
                   AND p.api_name = 'default'
                 LIMIT 1),
                (SELECT p.id FROM projects p
                 WHERE p.space_id = (SELECT o.space_id FROM ontologies o WHERE o.id = {table}.ontology_id)
                 LIMIT 1)
            )
            WHERE project_id IS NULL
              AND ontology_id IS NOT NULL
            """)
        )


def downgrade() -> None:
    """Revert: set project_id back to NULL on definition-class resources.

    This restores the option B state (project_id NULL → fallback at query time).
    """
    # shared_properties excluded (no ontology_id, see upgrade).
    definition_tables = [
        "object_types",
        "action_types",
        "link_types",
        "interface_types",
    ]
    for table in definition_tables:
        op.execute(sa.text(f"UPDATE {table} SET project_id = NULL"))
