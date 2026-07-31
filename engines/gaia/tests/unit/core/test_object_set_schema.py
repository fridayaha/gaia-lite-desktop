"""Unit tests for ObjectSet IR schema (graph-reasoning-design.md §7.2)."""

import pytest
from pydantic import ValidationError

from ontology.core.schemas.object_set import Filter, ObjectSetIR


class TestFilter:
    def test_exact_match(self):
        f = Filter(field="status", op="exactMatch", value="ACTIVE")
        assert f.value == "ACTIVE"

    def test_range_requires_dict_value(self):
        with pytest.raises(ValidationError):
            Filter(field="x", op="range", value="not-dict")
        f = Filter(field="x", op="range", value={"min": 1, "max": 10})
        assert f.value == {"min": 1, "max": 10}

    def test_within_polygon_requires_coords(self):
        with pytest.raises(ValidationError):
            Filter(field="loc", op="withinPolygon")
        f = Filter(field="loc", op="withinPolygon", coords=[[0, 0], [1, 0], [1, 1]])
        assert f.coords is not None

    def test_within_distance_requires_center_and_dist(self):
        with pytest.raises(ValidationError):
            Filter(field="loc", op="withinDistance", center=[0, 0])
        f = Filter(field="loc", op="withinDistance", center=[116.4, 39.9], max_distance=1000)
        assert f.max_distance == 1000

    def test_time_range_requires_dict(self):
        with pytest.raises(ValidationError):
            Filter(field="ts", op="timeRange", value="x")
        Filter(field="ts", op="timeRange", value={"start": "2026-01-01", "end": "2026-12-31"})


class TestObjectSetIR:
    def test_object_type_requires_object_type(self):
        with pytest.raises(ValidationError):
            ObjectSetIR(type="objectType")
        ir = ObjectSetIR(type="objectType", object_type="Supplier")
        assert ir.object_type == "Supplier"

    def test_static_requires_objects(self):
        with pytest.raises(ValidationError):
            ObjectSetIR(type="static")
        ir = ObjectSetIR(type="static", objects=["S1", "S2"])
        assert ir.objects == ["S1", "S2"]

    def test_filter_requires_object_set_and_filters(self):
        with pytest.raises(ValidationError):
            ObjectSetIR(type="filter", filters=[])
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="static", objects=["S1"]),
            filters=[Filter(field="status", op="exactMatch", value="ACTIVE")],
        )
        assert ir.object_set.type == "static"

    def test_search_around_requires_object_set_and_link(self):
        with pytest.raises(ValidationError):
            ObjectSetIR(type="searchAround", link="supplies")
        ir = ObjectSetIR(
            type="searchAround",
            link="supplies",
            object_set=ObjectSetIR(type="static", objects=["S1"]),
        )
        assert ir.link == "supplies"

    def test_search_around_depth(self):
        # 3 层嵌套 searchAround。
        ir = ObjectSetIR(
            type="searchAround", link="l3",
            object_set=ObjectSetIR(
                type="searchAround", link="l2",
                object_set=ObjectSetIR(
                    type="searchAround", link="l1",
                    object_set=ObjectSetIR(type="static", objects=["S1"]),
                ),
            ),
        )
        assert ir.search_around_depth() == 3

    def test_search_around_depth_zero_for_non_search(self):
        ir = ObjectSetIR(type="objectType", object_type="Supplier")
        assert ir.search_around_depth() == 0

    def test_nested_filter_inside_search_around(self):
        """供应链中断传导示例结构（§7.5）。"""
        ir = ObjectSetIR(
            type="searchAround",
            link="supplies",
            object_set=ObjectSetIR(
                type="filter",
                filters=[Filter(field="status", op="exactMatch", value="unfulfilled")],
                object_set=ObjectSetIR(
                    type="searchAround",
                    link="supplies",
                    object_set=ObjectSetIR(type="static", objects=["S001"]),
                ),
            ),
        )
        assert ir.search_around_depth() == 2
        assert ir.object_set.type == "filter"


class TestNewSchemaCapabilities:
    """新 IR 能力 schema 校验：集合运算 + order_by + 新 filter op。"""

    def test_union_valid(self):
        ir = ObjectSetIR(
            type="union",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1"]),
                ObjectSetIR(type="static", objects=["v2"]),
            ],
        )
        assert ir.type == "union"
        assert len(ir.object_sets) == 2

    def test_union_requires_two_sets(self):
        with pytest.raises(ValueError, match="union requires object_sets"):
            ObjectSetIR(type="union", object_sets=[ObjectSetIR(type="static", objects=["v1"])])

    def test_intersect_valid(self):
        ir = ObjectSetIR(
            type="intersect",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1"]),
                ObjectSetIR(type="static", objects=["v2"]),
            ],
        )
        assert ir.type == "intersect"

    def test_subtract_valid(self):
        ir = ObjectSetIR(
            type="subtract",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1"]),
                ObjectSetIR(type="static", objects=["v2"]),
            ],
        )
        assert ir.type == "subtract"

    def test_order_by_field(self):
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            order_by=[{"field": "createdAt", "desc": True}],
        )
        assert ir.order_by[0]["field"] == "createdAt"
        assert ir.order_by[0]["desc"] is True

    def test_in_filter_requires_list(self):
        with pytest.raises(ValueError, match="in requires value"):
            Filter(field="status", op="in", value="A")  # type: ignore[arg-type]

    def test_not_in_filter_requires_list(self):
        with pytest.raises(ValueError, match="notIn requires value"):
            Filter(field="status", op="notIn", value="A")  # type: ignore[arg-type]

    def test_greater_than_filter_valid(self):
        f = Filter(field="amt", op="greaterThan", value=100)
        assert f.op == "greaterThan"

    def test_starts_with_filter_valid(self):
        f = Filter(field="name", op="startsWith", value="Ac")
        assert f.op == "startsWith"

    def test_new_ops_in_enum(self):
        """新 op 都在 FilterOp 枚举内。"""
        for op in ["notEqual", "in", "notIn", "greaterThan", "lessThan", "startsWith", "endsWith"]:
            f = Filter(field="x", op=op, value=["v"] if op in ("in", "notIn") else "v")  # type: ignore[arg-type]
            assert f.op == op


class TestWhereClause:
    """where 嵌套逻辑组合 schema 测试（对齐 Palantir SearchJsonQueryV2）。"""

    def test_and_clause(self):
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            where={"type": "and", "value": [
                {"field": "a", "op": "exactMatch", "value": "1"},
                {"field": "b", "op": "exactMatch", "value": "2"},
            ]},
        )
        assert ir.where.type == "and"
        assert len(ir.where.value) == 2

    def test_or_clause(self):
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            where={"type": "or", "value": [
                {"field": "a", "op": "exactMatch", "value": "1"},
                {"field": "a", "op": "exactMatch", "value": "2"},
            ]},
        )
        assert ir.where.type == "or"

    def test_not_clause(self):
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            where={"type": "not", "value": {"field": "a", "op": "exactMatch", "value": "1"}},
        )
        assert ir.where.type == "not"
        assert ir.where.value.op == "exactMatch"

    def test_nested_and_or_not(self):
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            where={"type": "and", "value": [
                {"type": "or", "value": [
                    {"field": "a", "op": "exactMatch", "value": "1"},
                    {"field": "a", "op": "exactMatch", "value": "2"},
                ]},
                {"type": "not", "value": {"field": "b", "op": "isNull"}},
            ]},
        )
        assert ir.where.value[0].type == "or"
        assert ir.where.value[1].type == "not"

    def test_filter_accepts_where_or_filters(self):
        # filter 只要 filters 或 where 之一即可
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            where={"type": "not", "value": {"field": "a", "op": "isNull"}},
        )
        assert ir.where is not None

    def test_with_properties_schema(self):
        ir = ObjectSetIR(
            type="withProperties",
            object_set=ObjectSetIR(type="objectType", object_type="S"),
            derived_properties={"score": {"expression": "amount * 2", "type": "number"}},
        )
        assert ir.derived_properties["score"]["expression"] == "amount * 2"

    def test_reference_schema(self):
        ir = ObjectSetIR(type="reference", reference="ri.object-set.main.abc123")
        assert ir.reference == "ri.object-set.main.abc123"

    def test_with_properties_requires_derived(self):
        with pytest.raises(ValueError, match="withProperties requires"):
            ObjectSetIR(type="withProperties", object_set=ObjectSetIR(type="static", objects=["v1"]))

    def test_reference_requires_rid(self):
        with pytest.raises(ValueError, match="reference requires"):
            ObjectSetIR(type="reference")


class TestInterfaceIR:
    """interfaceBase / interfaceLinkSearchAround IR schema 测试。"""

    def test_interface_base(self):
        ir = ObjectSetIR(type="interfaceBase", interface="Geolocated")
        assert ir.interface == "Geolocated"

    def test_interface_base_requires_interface(self):
        with pytest.raises(ValueError, match="interfaceBase requires"):
            ObjectSetIR(type="interfaceBase")

    def test_interface_link_search_around(self):
        ir = ObjectSetIR(
            type="interfaceLinkSearchAround",
            interface="Geolocated",
            link="supplies",
            object_set=ObjectSetIR(type="static", objects=["v1"]),
            hops=[1, 2],
            direction="out",
        )
        assert ir.interface == "Geolocated"
        assert ir.link == "supplies"

    def test_interface_link_requires_all(self):
        with pytest.raises(ValueError, match="interfaceLinkSearchAround requires"):
            ObjectSetIR(
                type="interfaceLinkSearchAround",
                interface="Geolocated",
                object_set=ObjectSetIR(type="static", objects=["v1"]),
            )  # 缺 link
