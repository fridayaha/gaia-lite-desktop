"""adopt palantir rid for object_state and object_links

Revision ID: dc22d664a7f9
Revises: d4b5e1f6a7c8
Create Date: 2026-07-25 10:58:05.363105+00:00

对象身份模型从裸 UUID 主键改为 Palantir Resource Identifier (RID) 规范。

变更内容 (handoff-rid-migration.md §五 PR 2):

* ``object_state.object_id`` (VARCHAR(64) PK) → ``rid`` (VARCHAR(128) PK)
* ``object_links.source_object_id`` (VARCHAR(64)) → ``source_rid`` (VARCHAR(128))
* ``object_links.target_object_id`` (VARCHAR(64)) → ``target_rid`` (VARCHAR(128))
* 重建 object_links 的 source/target 索引 + uq_object_links 唯一约束 (列名变了)

采用 RENAME COLUMN + ALTER COLUMN TYPE 而非 add/drop column:
- 保留数据 (存量 object_state/object_links 行的值原样保留, 只是列名变了)
- 保留 PK 约束 (PK 跟随列 rename, 无需重建)
- 索引/唯一约束的列引用在 rename 后仍指向旧列名, 必须 drop + recreate 指向新列名

长度 64 → 128: RID 比 UUID 长 (ri.ontology.main.object.{uuid} ≈ 61 字符;
VIRTUAL 合成 rid ≈ 70+ 字符), 128 留足余量。

注意: 存量数据里的 object_id 值仍是裸 UUID (非 RID 格式), 这是预期的 ——
本次迁移只改 schema (列名+长度), 不回填历史数据。handoff §7.1 明确"存量可清空",
新生成的对象才会有 RID 格式。若需回填, 应另起 migration (不在本 PR 范围)。

downgrade 逆向: rid → object_id, 长度 128 → 64 (若存量 rid 超过 64 字符会截断报错,
downgrade 仅用于回滚空表场景)。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'dc22d664a7f9'
down_revision: str | Sequence[str] | None = 'd4b5e1f6a7c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── object_state: object_id → rid (VARCHAR(64) → VARCHAR(128)) ──
    # PK 约束 object_state_pkey 跟随列 rename, 无需重建。只改列名 + 扩长度。
    op.alter_column(
        'object_state', 'object_id',
        new_column_name='rid',
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )

    # ── object_links: source_object_id → source_rid ──
    # 先 drop 引用旧列名的索引, 再 rename + alter type, 最后 recreate 索引指向新列名。
    op.drop_index('ix_object_links_source_object_id', table_name='object_links')
    op.alter_column(
        'object_links', 'source_object_id',
        new_column_name='source_rid',
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_index('ix_object_links_source_rid', 'object_links', ['source_rid'], unique=False)

    # ── object_links: target_object_id → target_rid ──
    op.drop_index('ix_object_links_target_object_id', table_name='object_links')
    op.alter_column(
        'object_links', 'target_object_id',
        new_column_name='target_rid',
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_index('ix_object_links_target_rid', 'object_links', ['target_rid'], unique=False)

    # ── object_links: uq_object_links 唯一约束 (列名变了, 重建) ──
    op.drop_constraint('uq_object_links', 'object_links', type_='unique')
    op.create_unique_constraint(
        'uq_object_links', 'object_links',
        ['link_type_api_name', 'source_rid', 'target_rid'],
    )


def downgrade() -> None:
    # 逆向: rid → object_id, 长度 128 → 64。
    # ⚠️ 若存量 rid 值超过 64 字符 (VIRTUAL rid ≈ 70+), alter type 会失败 ——
    # downgrade 仅用于回滚空表或全 MANAGED rid (≤61 字符) 场景。
    #
    # 顺序与 upgrade 相反: 先 rename 列回旧名 + 缩长度, 再重建引用旧列名的索引/约束
    # (索引/约束在 upgrade 时被 drop, downgrade 末尾 recreate 指向旧列名)。

    # ── object_links: source_rid → source_object_id ──
    op.drop_index('ix_object_links_source_rid', table_name='object_links')
    op.alter_column(
        'object_links', 'source_rid',
        new_column_name='source_object_id',
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
    op.create_index('ix_object_links_source_object_id', 'object_links', ['source_object_id'], unique=False)

    # ── object_links: target_rid → target_object_id ──
    op.drop_index('ix_object_links_target_rid', table_name='object_links')
    op.alter_column(
        'object_links', 'target_rid',
        new_column_name='target_object_id',
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
    op.create_index('ix_object_links_target_object_id', 'object_links', ['target_object_id'], unique=False)

    # ── object_links: uq_object_links 唯一约束 (列已 rename 回旧名, 重建指向旧列名) ──
    op.drop_constraint('uq_object_links', 'object_links', type_='unique')
    op.create_unique_constraint(
        'uq_object_links', 'object_links',
        ['link_type_api_name', 'source_object_id', 'target_object_id'],
    )

    # ── object_state: rid → object_id (PK 跟随列 rename) ──
    op.alter_column(
        'object_state', 'rid',
        new_column_name='object_id',
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
