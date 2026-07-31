"""add constraints jsonb to properties table (VECTOR property config)

Revision ID: b2c3d4e5f6a7
Revises: 8549c04f46b9, e5b2c3d4f6a8
Create Date: 2026-07-13 14:00:00.000000+00:00

Stores VECTOR property configuration (dimension / similarity_function /
source_expression) for data_type=VECTOR properties. See implementation-status
§14.4 (semantic search). Non-VECTOR properties keep an empty dict default.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = ("8549c04f46b9", "e5b2c3d4f6a8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add constraints column as JSONB, default empty dict. server_default='{}'
    # so existing rows get '{}' immediately without a separate backfill step.
    op.add_column(
        "properties",
        sa.Column(
            "constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("properties", "constraints")
