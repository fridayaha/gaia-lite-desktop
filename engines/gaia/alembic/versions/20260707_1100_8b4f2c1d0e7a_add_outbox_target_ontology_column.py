"""add outbox.target_ontology column for sync outbox (INDEX/ARCHIVE) routing

Revision ID: 8b4f2c1d0e7a
Revises: 7a3c1e9b2d44
Create Date: 2026-07-07 11:00:00.000000+00:00

action-sync-outbox-design.md §8.1: outbox 表复用承载 Action 同步链路
(INDEX→Doris 近实时 / ARCHIVE→Iceberg 微批)。新增 target_ontology 列作为
ARCHIVE 分桶键,并加联合索引支撑 OutboxExecutor (排除 ARCHIVE) 与
SyncFlushScheduler (只取 ARCHIVE) 的 claim 查询。

effect_type 复用:新增 'INDEX' / 'ARCHIVE' 大写枚举值,与历史 WEBHOOK/
WRITE_BACK/SUB_ACTION/KAFKA_TOPIC/NOTIFICATION 共表,靠 effect_type 过滤
互不干扰 (design §3.1)。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b4f2c1d0e7a"
down_revision: str | Sequence[str] | None = "7a3c1e9b2d44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # target_ontology: ARCHIVE 分桶键 (ontology api_name)。
    # nullable=True 因为历史 effect_type (WEBHOOK/...) 的记录没有本体维度。
    op.add_column("outbox", sa.Column("target_ontology", sa.String(length=255), nullable=True))
    # 联合索引:支撑两种 claim 查询
    #   - OutboxExecutor: WHERE effect_type IN (...) AND status='PENDING' ...
    #   - SyncFlushScheduler: WHERE effect_type='ARCHIVE' AND status='PENDING'
    #     AND target_ontology=:ont
    # created_at 加入尾部以稳定排序 (FIFO 消费)。
    op.create_index(
        "ix_outbox_sync_claim",
        "outbox",
        ["effect_type", "status", "target_ontology", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_sync_claim", table_name="outbox")
    op.drop_column("outbox", "target_ontology")
