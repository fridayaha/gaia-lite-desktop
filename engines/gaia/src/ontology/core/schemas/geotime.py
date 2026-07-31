"""pydantic v2 schemas for GeoTime Layer (graph-reasoning-design.md §5).

内部数据结构，GeoTimeStore 与 DataFrameQueryService 间传递。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# 空间查询算子（对齐 ObjectSet IR Filter.op）。
SpatialOp = Literal["withinDistance", "withinPolygon", "withinBoundingBox", "intersects"]


class SpatialFilter(BaseModel):
    """空间过滤条件（PostGIS GiST 索引利用）。"""

    op: SpatialOp
    # withinPolygon: 多边形顶点 [[lon, lat], ...]（首尾自动闭合）
    coords: list[list[float]] | None = None
    # withinBoundingBox: [[minLon, minLat], [maxLon, maxLat]]
    bbox: list[list[float]] | None = None
    # withinDistance: 中心点 [lon, lat] + 半径（米）
    center: list[float] | None = None
    max_distance: float | None = None  # 米


class AggSpec(BaseModel):
    """时序聚合规范（TimescaleDB continuous aggregate 友好）。"""

    field: str
    func: Literal["avg", "sum", "min", "max", "count", "first", "last"]
    alias: str | None = None


class SeriesRow(BaseModel):
    """时序查询返回行。"""

    series_id: str
    timestamp: datetime
    location: list[float] | None = None  # [lon, lat]
    metrics: dict[str, Any] = Field(default_factory=dict)
