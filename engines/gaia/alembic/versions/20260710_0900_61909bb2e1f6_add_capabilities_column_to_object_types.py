"""add capabilities column to object_types

Revision ID: 61909bb2e1f6
Revises: 2fb043f8ca64
Create Date: 2026-07-10 09:00:34.225467+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '61909bb2e1f6'
down_revision: Union[str, Sequence[str], None] = '2fb043f8ca64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add capabilities JSONB column. Existing rows get '{}' (all-disabled
    # default) — only Doris base index is on by default for MANAGED types
    # (red line #4), graph/geotime require explicit opt-in.
    # server_default is used to populate existing rows in one statement;
    # the ORM model uses default=dict for new inserts (Python-side).
    op.add_column(
        'object_types',
        sa.Column(
            'capabilities',
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
            ),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Drop the server_default after backfill — the ORM manages defaults
    # in Python (default=dict), and alembic check would flag the mismatch.
    op.alter_column(
        'object_types',
        'capabilities',
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column('object_types', 'capabilities')
