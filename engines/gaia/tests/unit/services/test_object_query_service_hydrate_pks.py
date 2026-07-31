"""PR 5a: ObjectQueryService.hydrate_by_pks 单测（§7.7 VIRTUAL 批量水合）。

测试覆盖：
- 空列表 / 单批 / 分批（>1000）
- Trino 查询失败抛异常（调用方 catch 后标 _partial）
- select_fields 下推到 SELECT 列表
- PK 列必选（即使 select_fields 不含）
- MANAGED OT 拒绝（走 hydrate_by_rids）
- 类型强转 + datetime/decimal 规范化
- 物理列名 → api_name 映射
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.ontology import (
    BackingColumnRef,
    DataType,
    ObjectType,
    PropertyDef,
)
from ontology.services.object_query_service import ObjectQueryService


def _virtual_ot(
    api_name: str = "Customer",
    pk_api: str = "customerId",
    extra_props: list[tuple[str, DataType, str]] | None = None,
) -> ObjectType:
    """构造 VIRTUAL ObjectType（含 backing_mapping）。"""
    now = datetime.now(UTC)
    props = [
        PropertyDef(
            id=f"p-{api_name}-{pk_api}",
            object_type_id=f"ot-{api_name}",
            api_name=pk_api,
            display_name=pk_api,
            description="",
            data_type=DataType.STRING,
            is_primary_key=True,
            is_title_property=True,
            nullable=False,
            indexed=False,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
            backing_mapping=BackingColumnRef(
                dataset_api_name="ds1",
                backing_catalog="pgnative",
                backing_schema="public",
                backing_table="customers",
                backing_column=pk_api,
            ),
        ),
    ]
    for api, dt, col in extra_props or [("name", DataType.STRING, "name")]:
        props.append(
            PropertyDef(
                id=f"p-{api_name}-{api}",
                object_type_id=f"ot-{api_name}",
                api_name=api,
                display_name=api,
                description="",
                data_type=dt,
                is_primary_key=False,
                is_title_property=False,
                nullable=True,
                indexed=False,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
                backing_mapping=BackingColumnRef(
                    dataset_api_name="ds1",
                    backing_catalog="pgnative",
                    backing_schema="public",
                    backing_table="customers",
                    backing_column=col,
                ),
            )
        )
    return ObjectType(
        id=f"ot-{api_name}",
        ontology_id="ont-1",
        api_name=api_name,
        display_name=api_name,
        description="",
        primary_key=pk_api,
        title_property=pk_api,
        storage_type="VIRTUAL",
        created_at=now,
        updated_at=now,
        properties=props,
    )


def _make_svc(
    *,
    query_return: list[dict] | None = None,
    query_side_effect=None,
) -> ObjectQueryService:
    """构造 ObjectQueryService，mock 所有依赖。"""
    mock_metadata = AsyncMock()
    mock_metadata.list_datasources = AsyncMock(return_value=[])
    svc = ObjectQueryService(
        metadata=mock_metadata,
        catalog=AsyncMock(),
        index=AsyncMock(),
        dataset=AsyncMock(),
        engine=AsyncMock(),
    )
    if query_side_effect is not None:
        svc._engine.query = AsyncMock(side_effect=query_side_effect)
    else:
        svc._engine.query = AsyncMock(return_value=query_return or [])
    return svc


class TestHydrateByPks:
    """hydrate_by_pks 批量水合单测。"""

    async def test_empty_pks_returns_empty(self):
        """空 PK 列表 → 不查 Trino，返回空。"""
        svc = _make_svc()
        ot = _virtual_ot()
        result = await svc.hydrate_by_pks("SC", ot, [])
        assert result == []
        svc._engine.query.assert_not_awaited()

    async def test_single_batch_single_query(self):
        """单批 PK（< 1000）→ 单次 Trino 查询。"""
        rows = [
            {"customerId": "C001", "name": "Acme"},
            {"customerId": "C002", "name": "Globex"},
        ]
        svc = _make_svc(query_return=rows)
        ot = _virtual_ot()
        result = await svc.hydrate_by_pks("SC", ot, ["C001", "C002"])
        assert len(result) == 2
        assert result[0]["name"] == "Acme"
        assert result[1]["name"] == "Globex"
        # 单次查询
        assert svc._engine.query.await_count == 1
        # SQL 含 IN (?, ?)
        sql = svc._engine.query.call_args.args[0]
        assert "IN (?, ?)" in sql
        # params 是 PK 列表
        params = svc._engine.query.call_args.args[1]
        assert params == ["C001", "C002"]

    async def test_batch_over_1000_splits(self):
        """PK > 1000 → 分批查询（每批 ≤ 1000）。"""
        pks = [f"C{i:04d}" for i in range(2500)]
        # 每次查询返回空（只验证分批调用次数）
        svc = _make_svc(query_return=[])
        ot = _virtual_ot()
        await svc.hydrate_by_pks("SC", ot, pks)
        # 2500 / 1000 = 3 批（1000 + 1000 + 500）
        assert svc._engine.query.await_count == 3

    async def test_trino_failure_raises(self):
        """Trino 查询失败 → 抛异常（调用方 catch 后标 _partial）。"""
        svc = _make_svc(query_side_effect=RuntimeError("Trino down"))
        ot = _virtual_ot()
        with pytest.raises(RuntimeError, match="Trino down"):
            await svc.hydrate_by_pks("SC", ot, ["C001"])

    async def test_select_fields_pushed_down(self):
        """select_fields → SELECT 列表只含指定字段 + PK 列。"""
        ot = _virtual_ot(
            extra_props=[("name", DataType.STRING, "name"), ("status", DataType.STRING, "status")],
        )
        svc = _make_svc(query_return=[{"customerId": "C001", "name": "A"}])
        await svc.hydrate_by_pks("SC", ot, ["C001"], select_fields=["name"])
        sql = svc._engine.query.call_args.args[0]
        # SELECT 含 customerId（PK 必选）和 name，不含 status
        assert '"customerId"' in sql
        assert '"name"' in sql
        assert '"status"' not in sql

    async def test_pk_always_selected_with_select_fields(self):
        """select_fields 不含 PK → PK 列仍被选（用于 rid 回填）。"""
        ot = _virtual_ot()
        svc = _make_svc(query_return=[{"customerId": "C001", "name": "A"}])
        await svc.hydrate_by_pks("SC", ot, ["C001"], select_fields=["name"])
        sql = svc._engine.query.call_args.args[0]
        assert '"customerId"' in sql  # PK 必选

    async def test_type_coercion_string_to_int(self):
        """BIGINT 列存为字符串 → 强转为 int（_coerce_property_types）。"""
        ot = _virtual_ot(
            extra_props=[("amount", DataType.LONG, "amount")],
        )
        svc = _make_svc(query_return=[{"customerId": "C001", "amount": "100"}])
        result = await svc.hydrate_by_pks("SC", ot, ["C001"])
        assert result[0]["amount"] == 100
        assert isinstance(result[0]["amount"], int)

    async def test_datetime_normalized_to_isoformat(self):
        """datetime 值 → isoformat 字符串（JSON 可序列化）。"""
        ot = _virtual_ot()
        ts = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
        svc = _make_svc(query_return=[{"customerId": "C001", "name": ts}])
        result = await svc.hydrate_by_pks("SC", ot, ["C001"])
        assert result[0]["name"] == "2026-01-15T10:30:00+00:00"

    async def test_decimal_normalized_to_float(self):
        """Decimal 值 → float（JSON 可序列化）。"""
        ot = _virtual_ot()
        svc = _make_svc(query_return=[{"customerId": "C001", "name": Decimal("3.14")}])
        result = await svc.hydrate_by_pks("SC", ot, ["C001"])
        assert result[0]["name"] == 3.14
        assert isinstance(result[0]["name"], float)

    async def test_managed_ot_rejected(self):
        """MANAGED OT → ValueError（应走 hydrate_by_rids）。"""
        ot = _virtual_ot()
        ot = ot.model_copy(update={"storage_type": "MANAGED"})
        svc = _make_svc()
        with pytest.raises(ValueError, match="only supports VIRTUAL"):
            await svc.hydrate_by_pks("SC", ot, ["C001"])

    async def test_no_properties_returns_empty(self):
        """OT 无 properties → 返回空（无法构造 SELECT）。"""
        ot = _virtual_ot()
        ot = ot.model_copy(update={"properties": []})
        svc = _make_svc()
        result = await svc.hydrate_by_pks("SC", ot, ["C001"])
        assert result == []
        svc._engine.query.assert_not_awaited()

    async def test_physical_column_mapped_to_api_name(self):
        """物理列名（snake_case）→ api_name（camelCase）映射。"""
        now = datetime.now(UTC)
        ot = ObjectType(
            id="ot-1",
            ontology_id="ont-1",
            api_name="Flight",
            display_name="Flight",
            description="",
            primary_key="flightId",
            title_property="flightId",
            storage_type="VIRTUAL",
            created_at=now,
            updated_at=now,
            properties=[
                PropertyDef(
                    id="p1",
                    object_type_id="ot-1",
                    api_name="flightId",
                    display_name="flightId",
                    description="",
                    data_type=DataType.STRING,
                    is_primary_key=True,
                    is_title_property=True,
                    nullable=False,
                    indexed=False,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                    backing_mapping=BackingColumnRef(
                        dataset_api_name="ds1",
                        backing_catalog="pgnative",
                        backing_schema="public",
                        backing_table="flights",
                        backing_column="flight_id",
                    ),
                ),
            ],
        )
        # Trino 返回物理列名 flight_id
        svc = _make_svc(query_return=[{"flight_id": "F001"}])
        result = await svc.hydrate_by_pks("SC", ot, ["F001"])
        # 出口应是 api_name flightId
        assert result[0]["flightId"] == "F001"


class TestHydrateByRidsVirtualDelegates:
    """hydrate_by_rids 对 VIRTUAL 委托 hydrate_by_pks（PR 5a）。"""

    async def test_virtual_hydrate_by_rids_delegates_to_hydrate_by_pks(self):
        """VIRTUAL OT 的 hydrate_by_rids → 委托 hydrate_by_pks（不再 NotImplementedError）。"""
        from ontology.core.rid import generate_virtual_rid

        ot = _virtual_ot()
        svc = _make_svc(query_return=[{"customerId": "C001", "name": "Acme"}])
        rids = [generate_virtual_rid("SC", "Customer", "C001")]
        result = await svc.hydrate_by_rids("SC", rids, ot)
        assert len(result) == 1
        assert result[0]["name"] == "Acme"
