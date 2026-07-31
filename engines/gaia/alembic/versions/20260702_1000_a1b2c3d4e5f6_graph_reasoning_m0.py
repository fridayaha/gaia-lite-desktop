"""graph-reasoning: link_types edge semantics + analysis_records

Revision ID: 9575abae4046
Revises: 9575abae4046
Create Date: 2026-07-02 10:00:00.000000+00:00

Graph-reasoning 特性 M0 schema 变更（graph-reasoning-design.md §3.2, §3.4）：

1. link_types 表新增两列（图遍历必需的边语义，C1 迁移口子）：
   - weight_property VARCHAR(255) NULL：权重属性名（指向边属性），路径推理加权
   - temporal BOOLEAN DEFAULT FALSE：是否时态关系（含有效期）
   时态边的 start_time/end_time 作为边属性存储（Neo4j 关系属性 /
   object_links JSONB），不作为 LinkType 固定列，故不增列。

2. 新增 analysis_records 表（证据链快照，C11 合规溯源轻量版）：
   每次推理查询生成一条记录，含 ObjectSet IR + 各步引擎结果摘要 +
   命中对象的血缘指针。

DataType 枚举扩展（GEOPOINT/GEOSHAPE 激活 + 新增 GEOTEMPORAL_SERIES/
TIME_SERIES）无需迁移：data_type 列本就是 VARCHAR(50)，枚举值由
pydantic 层校验，存量数据不受影响（仅加枚举值，不改存量数据）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9575abae4046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. link_types: 新增边语义列（图遍历必需）
    op.add_column("link_types", sa.Column("weight_property", sa.String(length=255), nullable=True))
    op.add_column(
        "link_types",
        sa.Column("temporal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 2. analysis_records: 证据链快照表
    op.create_table(
        "analysis_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("ontology_id", sa.String(length=32), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False, server_default="anonymous"),
        sa.Column("object_set_ir", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_pointers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontologies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_analysis_records_ontology_id"), "analysis_records", ["ontology_id"], unique=False
    )
    op.create_index(
        op.f("ix_analysis_records_created_at"), "analysis_records", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_analysis_records_created_at"), table_name="analysis_records")
    op.drop_index(op.f("ix_analysis_records_ontology_id"), table_name="analysis_records")
    op.drop_table("analysis_records")
    op.drop_column("link_types", "temporal")
    op.drop_column("link_types", "weight_property")
