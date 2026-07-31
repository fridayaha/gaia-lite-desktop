"""pk→rid 翻译层（API 边界）单测。

验证 DataFrameQueryService._resolve_rids_by_pk / _resolve_rid_by_pk_any_type：
- Agent 传业务主键 → 系统内部解析为 object_state.rid（rid）
- 找不到的 pk 抛 NotFoundError（不静默返回空）
- 跨类型按 pk 扫描（find_paths 无 link_types 时）

这是 ADR-019 边界约束的守护测试：rid 不泄漏到 Agent，入参永远是业务主键。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import NotFoundError
from ontology.core.models.ontology import ObjectStateModel, ObjectTypeModel  # noqa: F401 (register ORM)
from ontology.services.object_set_executor import DataFrameQueryService


async def _seed_object_state(session, ot_api: str, pk_field: str, pk_val: str, rid: str):
    """插入一行 object_state（直接走 ORM，绕过 service 层）。"""
    session.add(
        ObjectStateModel(
            rid=rid,
            object_type_api_name=ot_api,
            ontology_id="ont-1",
            ontology_api_name="SC",
            version=1,
            properties={pk_field: pk_val, "name": f"obj-{pk_val}"},
        )
    )
    await session.commit()


def _make_ot(api_name: str, primary_key: str) -> ObjectTypeModel:
    """构造一个最小 ObjectTypeModel（只填翻译层需要的字段）。"""
    ot = MagicMock(spec=ObjectTypeModel)
    ot.api_name = api_name
    ot.primary_key = primary_key
    ot.id = f"ot-{api_name}"
    return ot


async def _seed(db_session):
    """插入 object_state 行：Supplier S001/S002，Order ORD-1。"""
    await _seed_object_state(db_session, "Supplier", "supplier_id", "S001", "rid-sup-1")
    await _seed_object_state(db_session, "Supplier", "supplier_id", "S002", "rid-sup-2")
    await _seed_object_state(db_session, "Order", "order_id", "ORD-1", "rid-ord-1")


def _svc_with_real_session(db_session, ots: list) -> DataFrameQueryService:
    """构造 DataFrameQueryService：object_state 查询走真 session，
    get_object_type / list_object_types 走 mock（避免卷入 project_id FK）。"""
    meta = MagicMock()
    meta._session = db_session
    meta.get_object_type = AsyncMock(return_value=_make_ot("Supplier", "supplier_id"))
    meta.list_object_types = AsyncMock(return_value=ots)
    return DataFrameQueryService(graph_store=None, geotime_store=None, metadata=meta)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_rids_by_pk_single(db_session):
    """单个 pk 正确解析为 rid。"""
    await _seed(db_session)
    svc = _svc_with_real_session(db_session, [])

    rids = await svc._resolve_rids_by_pk("SC", "Supplier", ["S001"])
    assert rids == ["rid-sup-1"]


@pytest.mark.asyncio
async def test_resolve_rids_by_pk_batch_preserves_order(db_session):
    """批量 pk 解析，返回顺序与入参一致。"""
    await _seed(db_session)
    svc = _svc_with_real_session(db_session, [])

    rids = await svc._resolve_rids_by_pk("SC", "Supplier", ["S002", "S001"])
    assert rids == ["rid-sup-2", "rid-sup-1"]


@pytest.mark.asyncio
async def test_resolve_rids_by_pk_unknown_raises_not_found(db_session):
    """不存在的 pk 抛 NotFoundError，不静默返回空（禁止静默失败）。"""
    await _seed(db_session)
    svc = _svc_with_real_session(db_session, [])

    with pytest.raises(NotFoundError, match="NOPE"):
        await svc._resolve_rids_by_pk("SC", "Supplier", ["S001", "NOPE"])


@pytest.mark.asyncio
async def test_resolve_rids_by_pk_empty_returns_empty(db_session):
    """空 pk 列表直接返回空，不查 DB。"""
    await _seed(db_session)
    svc = _svc_with_real_session(db_session, [])

    assert await svc._resolve_rids_by_pk("SC", "Supplier", []) == []


@pytest.mark.asyncio
async def test_resolve_rid_by_pk_any_type_finds_across_types(db_session):
    """无 ObjectType 上下文时跨类型按 pk 扫描（find_paths 无 link_types 路径）。"""
    await _seed(db_session)
    ots = [_make_ot("Supplier", "supplier_id"), _make_ot("Order", "order_id")]
    svc = _svc_with_real_session(db_session, ots)

    # S001 在 Supplier，ORD-1 在 Order，都能扫到
    assert await svc._resolve_rid_by_pk_any_type("SC", "S001") == "rid-sup-1"
    assert await svc._resolve_rid_by_pk_any_type("SC", "ORD-1") == "rid-ord-1"


@pytest.mark.asyncio
async def test_resolve_rid_by_pk_any_type_unknown_raises(db_session):
    """跨类型扫描也找不到时抛 NotFoundError。"""
    await _seed(db_session)
    ots = [_make_ot("Supplier", "supplier_id"), _make_ot("Order", "order_id")]
    svc = _svc_with_real_session(db_session, ots)

    with pytest.raises(NotFoundError):
        await svc._resolve_rid_by_pk_any_type("SC", "GHOST")
