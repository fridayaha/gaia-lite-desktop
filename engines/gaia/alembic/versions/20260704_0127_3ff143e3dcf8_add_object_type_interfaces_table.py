"""add object_type_interfaces table

Revision ID: 3ff143e3dcf8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-04 01:27:19.186998+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ff143e3dcf8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "object_type_interfaces",
        sa.Column("object_type_id", sa.String(32), sa.ForeignKey("object_types.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("interface_type_id", sa.String(32), sa.ForeignKey("interface_types.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index(
        "ix_object_type_interfaces_interface_type_id",
        "object_type_interfaces",
        ["interface_type_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_object_type_interfaces_interface_type_id", table_name="object_type_interfaces")
    op.drop_table("object_type_interfaces")
