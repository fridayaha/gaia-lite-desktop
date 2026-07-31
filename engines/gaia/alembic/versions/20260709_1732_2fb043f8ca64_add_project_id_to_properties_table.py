"""add project_id to properties table

Revision ID: 2fb043f8ca64
Revises: e5b2c3d4f6a8
Create Date: 2026-07-09 17:32:48.104838+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2fb043f8ca64'
down_revision: Union[str, Sequence[str], None] = 'e5b2c3d4f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Add project_id column (nullable first, for existing rows).
    op.add_column('properties', sa.Column('project_id', sa.String(32), nullable=True))
    # Step 2: Backfill existing rows with the first available project id.
    op.execute("""
        UPDATE properties SET project_id = (
            SELECT id FROM projects ORDER BY created_at LIMIT 1
        ) WHERE project_id IS NULL
    """)
    # Step 3: Set NOT NULL.
    op.alter_column('properties', 'project_id', nullable=False)
    # Step 4: Add FK constraint.
    op.create_foreign_key(
        'fk_properties_project', 'properties', 'projects',
        ['project_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('fk_properties_project', 'properties', type_='foreignkey')
    op.drop_column('properties', 'project_id')
