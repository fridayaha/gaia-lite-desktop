"""add_tenant_metadata

Revision ID: ce89fa2e4f30
Revises: caaee34d415d
Create Date: 2026-05-29 17:55:26.980221

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce89fa2e4f30'
down_revision: Union[str, Sequence[str], None] = 'caaee34d415d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hub_items', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('hub_items', sa.Column('workspace_id', sa.String(128), nullable=True))
    op.add_column('hub_items', sa.Column('visibility_scope', sa.String(50), nullable=True))

    op.add_column('hub_item_versions', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('hub_item_versions', sa.Column('workspace_id', sa.String(128), nullable=True))

    op.add_column('hub_item_relations', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('hub_item_relations', sa.Column('workspace_id', sa.String(128), nullable=True))

    op.add_column('approval_records', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('approval_records', sa.Column('workspace_id', sa.String(128), nullable=True))

    op.add_column('lifecycle_events', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('lifecycle_events', sa.Column('workspace_id', sa.String(128), nullable=True))

    op.add_column('scan_reports', sa.Column('organization_id', sa.String(128), nullable=True))
    op.add_column('scan_reports', sa.Column('workspace_id', sa.String(128), nullable=True))

    op.execute(
        "UPDATE hub_items SET organization_id = 'default', workspace_id = 'default', "
        "visibility_scope = 'workspace' WHERE workspace_id IS NULL"
    )

    op.create_index('ix_hub_items_workspace', 'hub_items', ['workspace_id'])
    op.create_index('ix_hub_items_org_workspace', 'hub_items', ['organization_id', 'workspace_id'])
    op.create_index('ix_hub_item_versions_workspace', 'hub_item_versions', ['workspace_id'])
    op.create_index('ix_scan_reports_workspace', 'scan_reports', ['workspace_id'])


def downgrade() -> None:
    op.drop_index('ix_scan_reports_workspace', table_name='scan_reports')
    op.drop_index('ix_hub_item_versions_workspace', table_name='hub_item_versions')
    op.drop_index('ix_hub_items_org_workspace', table_name='hub_items')
    op.drop_index('ix_hub_items_workspace', table_name='hub_items')

    op.drop_column('scan_reports', 'workspace_id')
    op.drop_column('scan_reports', 'organization_id')

    op.drop_column('lifecycle_events', 'workspace_id')
    op.drop_column('lifecycle_events', 'organization_id')

    op.drop_column('approval_records', 'workspace_id')
    op.drop_column('approval_records', 'organization_id')

    op.drop_column('hub_item_relations', 'workspace_id')
    op.drop_column('hub_item_relations', 'organization_id')

    op.drop_column('hub_item_versions', 'workspace_id')
    op.drop_column('hub_item_versions', 'organization_id')

    op.drop_column('hub_items', 'visibility_scope')
    op.drop_column('hub_items', 'workspace_id')
    op.drop_column('hub_items', 'organization_id')
