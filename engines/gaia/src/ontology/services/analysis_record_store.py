"""AnalysisRecord 证据链快照 (graph-reasoning-design.md §3.4, §10.2, M6).

每次推理查询（query_with_dataframe / ObjectSet IR 执行）生成一条记录：
- object_set_ir：ObjectSet IR 快照
- result_summary：各步引擎耗时 + 命中数 + truncated
- evidence_pointers：命中对象 backing_mapping 血缘指针

合规溯源轻量版：可追溯"谁在何时用何意图查到了哪些对象"。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    pass


class AnalysisRecord(BaseModel):
    """证据链快照记录（analysis_records 表的 pydantic 映射）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    principal: str = "anonymous"
    object_set_ir: dict[str, Any]
    result_summary: dict[str, Any]
    evidence_pointers: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AnalysisRecordStore:
    """证据链快照读写（薄包装 PostgresMetaStore session）。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def save(
        self,
        ontology_id: str,
        object_set_ir: dict[str, Any],
        result_summary: dict[str, Any],
        evidence_pointers: dict[str, Any],
        principal: str = "anonymous",
    ) -> str:
        """保存证据链快照，返回记录 id。"""
        from ontology.core.models.defaults import new_uuid
        from ontology.core.models.ontology import AnalysisRecordModel

        record = AnalysisRecordModel(
            id=new_uuid(),
            ontology_id=ontology_id,
            principal=principal,
            object_set_ir=object_set_ir,
            result_summary=result_summary,
            evidence_pointers=evidence_pointers,
        )
        self._session.add(record)
        await self._session.commit()
        return record.id

    async def get(self, record_id: str) -> AnalysisRecord | None:
        """按 id 查证据链快照。"""
        from sqlalchemy import select

        from ontology.core.models.ontology import AnalysisRecordModel

        stmt = select(AnalysisRecordModel).where(AnalysisRecordModel.id == record_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return AnalysisRecord.model_validate(model)
