"""backfill object_state.properties keys: api_name -> backing_column

Revision ID: 7a3c1e9b2d44
Revises: 046a1877a1cf
Create Date: 2026-07-07 09:30:00.000000+00:00

object_state.properties JSONB was previously keyed by the property api_name
(camelCase). The CDC chain (PG → Kafka → Doris) requires the JSONB key to
equal the Doris idx table column name (backing_column, snake_case) so that
Doris stream-load matches columns by name without per-table jsonpaths
(docs/bugfix/path-b-kafka-doris-schema-mismatch.md). This migration backfills
existing rows: for every (ontology, object_type) pair, rename each property
api_name key → its backing_column inside object_state.properties.

Properties without a backing_mapping (backing_column == api_name) are a no-op.
Keys not present on the ObjectType (extras like ``visibility``) are left as-is.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a3c1e9b2d44"
down_revision: str | Sequence[str] | None = "046a1877a1cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # For every (ontology_api_name, object_type_api_name) present in
    # object_state, look up that ObjectType's properties and rename each
    # api_name key → backing_column inside the properties JSONB (only when
    # they differ and a backing_column is declared).
    #
    # We iterate property_defs joined to object_types + ontologies so each
    # (ontology, object_type, api_name, backing_column) tuple is processed
    # once; the UPDATE renames the key inside matching object_state rows.
    op.execute(
        """
        WITH pairs AS (
            SELECT DISTINCT
                ont.api_name AS ont_api,
                ot.api_name  AS ot_api,
                pd.api_name  AS prop_api,
                pd.backing_column AS backing_col
            FROM properties pd
            JOIN object_types ot ON ot.id = pd.object_type_id
            JOIN ontologies ont ON ont.id = ot.ontology_id
            WHERE pd.backing_column IS NOT NULL
              AND pd.backing_column <> ''
              AND pd.api_name <> pd.backing_column
        )
        UPDATE object_state os
        SET properties = (properties - p.prop_api) || jsonb_build_object(p.backing_col, properties -> p.prop_api)
        FROM pairs p
        WHERE os.ontology_api_name = p.ont_api
          AND os.object_type_api_name = p.ot_api
          AND os.properties ? p.prop_api
        """
    )


def downgrade() -> None:
    # Reverse: rename backing_column → api_name.
    op.execute(
        """
        WITH pairs AS (
            SELECT DISTINCT
                ont.api_name AS ont_api,
                ot.api_name  AS ot_api,
                pd.api_name  AS prop_api,
                pd.backing_column AS backing_col
            FROM properties pd
            JOIN object_types ot ON ot.id = pd.object_type_id
            JOIN ontologies ont ON ont.id = ot.ontology_id
            WHERE pd.backing_column IS NOT NULL
              AND pd.backing_column <> ''
              AND pd.api_name <> pd.backing_column
        )
        UPDATE object_state os
        SET properties = (properties - p.backing_col) || jsonb_build_object(p.prop_api, properties -> p.backing_col)
        FROM pairs p
        WHERE os.ontology_api_name = p.ont_api
          AND os.object_type_api_name = p.ot_api
          AND os.properties ? p.backing_col
        """
    )
