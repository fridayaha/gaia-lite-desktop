"""Unit tests for operator_sql — SQL generation + injection guards (ADR-018 #3)."""

from __future__ import annotations

import pytest

from ontology.core.schemas.pipeline_builder import (
    FilterCondition,
    JoinCondition,
    NodeConfig,
    QualityRule,
    SortKey,
)
from ontology.layers.pipeline.operator_sql import (
    _safe_ident,
    _sql_literal,
    _validate_sql_type,
    build_quality_check_sql,
    build_transform_sql,
)


class TestSafeIdent:
    def test_valid_identifier(self) -> None:
        assert _safe_ident("my_table") == "my_table"

    def test_valid_dotted(self) -> None:
        assert _safe_ident("iceberg.my_table") == "iceberg.my_table"

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("raw; DROP TABLE x")

    def test_rejects_quote(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("raw' OR 1=1")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _safe_ident("")


class TestSqlLiteral:
    def test_string_escapes_quotes(self) -> None:
        assert _sql_literal("it's") == "'it''s'"

    def test_none_is_null(self) -> None:
        assert _sql_literal(None) == "NULL"

    def test_int(self) -> None:
        assert _sql_literal(42) == "42"

    def test_bool(self) -> None:
        assert _sql_literal(True) == "TRUE"


class TestValidateSqlType:
    def test_allowed_type(self) -> None:
        assert _validate_sql_type("INTEGER") == "INTEGER"

    def test_allowed_decimal_with_precision(self) -> None:
        assert _validate_sql_type("DECIMAL(10,2)") == "DECIMAL(10,2)"

    def test_rejects_injection(self) -> None:
        with pytest.raises(ValueError, match="Disallowed SQL type"):
            _validate_sql_type("EVIL; DROP TABLE x")


class TestTransformSql:
    def test_filter(self) -> None:
        config = NodeConfig(expression="status = 'active'")
        sql = build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")
        assert "WHERE status = 'active'" in sql
        assert "tfm_f1 AS" in sql

    def test_select_validates_columns(self) -> None:
        config = NodeConfig(columns=["col1", "col2"])
        sql = build_transform_sql("Select", config, ["src_s1"], "tfm_s1")
        assert "col1, col2" in sql

    def test_select_rejects_injection_column(self) -> None:
        config = NodeConfig(columns=["col; DROP TABLE x"])
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            build_transform_sql("Select", config, ["src_s1"], "tfm_s1")

    def test_unknown_operator_passthrough(self) -> None:
        config = NodeConfig()
        sql = build_transform_sql("UnknownOp", config, ["src_s1"], "tfm_u1")
        assert "SELECT * FROM src_s1" in sql

    # ── 结构化 Join 条件 ──
    def test_join_structured_conditions(self) -> None:
        config = NodeConfig(
            join_type="INNER",
            join_conditions=[
                JoinCondition(left_column="id", right_column="customer_id"),
                JoinCondition(left_column="region", right_column="region"),
            ],
        )
        sql = build_transform_sql("Join", config, ["src_a", "src_b"], "tfm_j1")
        assert "INNER JOIN src_b AS right ON" in sql
        assert "left.id = right.customer_id" in sql
        assert "left.region = right.region" in sql
        assert "AND" in sql

    def test_join_structured_rejects_injection(self) -> None:
        config = NodeConfig(
            join_type="INNER",
            join_conditions=[JoinCondition(left_column="id; DROP", right_column="cid")],
        )
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            build_transform_sql("Join", config, ["src_a", "src_b"], "tfm_j1")

    def test_join_legacy_string_backcompat(self) -> None:
        config = NodeConfig(join_type="LEFT", join_condition="left.id = right.cid")
        sql = build_transform_sql("Join", config, ["src_a", "src_b"], "tfm_j1")
        assert "LEFT JOIN src_b AS right ON left.id = right.cid" in sql

    # ── 结构化 Sort 键（ASC/DESC） ──
    def test_sort_structured_keys_with_direction(self) -> None:
        config = NodeConfig(
            sort_keys=[
                SortKey(column="region", direction="ASC"),
                SortKey(column="amount", direction="DESC"),
            ],
        )
        sql = build_transform_sql("Sort", config, ["src_s1"], "tfm_so1")
        assert "ORDER BY region ASC, amount DESC" in sql

    def test_sort_legacy_columns_backcompat(self) -> None:
        config = NodeConfig(columns=["region", "amount"])
        sql = build_transform_sql("Sort", config, ["src_s1"], "tfm_so1")
        assert "ORDER BY region, amount" in sql

    # ── 结构化 Filter 条件 ──
    def test_filter_structured_eq(self) -> None:
        config = NodeConfig(
            filter_conditions=[FilterCondition(column="status", operator="eq", value="active")]
        )
        sql = build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")
        assert "WHERE status = 'active'" in sql

    def test_filter_structured_is_null(self) -> None:
        config = NodeConfig(
            filter_conditions=[FilterCondition(column="email", operator="is_null")]
        )
        sql = build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")
        assert "WHERE email IS NULL" in sql

    def test_filter_structured_in(self) -> None:
        config = NodeConfig(
            filter_conditions=[FilterCondition(column="status", operator="in", values=["a", "b"])]
        )
        sql = build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")
        assert "status IN ('a', 'b')" in sql

    def test_filter_structured_rejects_injection(self) -> None:
        config = NodeConfig(
            filter_conditions=[FilterCondition(column="bad; DROP", operator="eq", value="x")]
        )
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")

    def test_filter_legacy_expression_backcompat(self) -> None:
        config = NodeConfig(expression="status = 'active'")
        sql = build_transform_sql("Filter", config, ["src_s1"], "tfm_f1")
        assert "WHERE status = 'active'" in sql

    # ── TypeCast 多列 ──
    def test_typecast_multi_column(self) -> None:
        config = NodeConfig(
            cast_columns=[
                {"column": "amount", "target_type": "DECIMAL(10,2)"},
                {"column": "qty", "target_type": "INTEGER"},
            ]
        )
        sql = build_transform_sql("TypeCast", config, ["src_s1"], "tfm_t1")
        assert "CAST(amount AS DECIMAL(10,2)) AS amount" in sql
        assert "CAST(qty AS INTEGER) AS qty" in sql
        assert "* REPLACE" in sql

    def test_typecast_legacy_single_column_backcompat(self) -> None:
        config = NodeConfig(target_type="INTEGER", extra={"column": "qty"})
        sql = build_transform_sql("TypeCast", config, ["src_s1"], "tfm_t1")
        assert "CAST(qty AS INTEGER) AS qty" in sql


class TestQualityCheckSql:
    def test_not_null_rule(self) -> None:
        rule = QualityRule(rule_type="not_null", field="email", config={}, severity="ERROR", message="")
        sql = build_quality_check_sql([rule], "tfm_s1")
        assert sql is not None
        assert "email IS NULL" in sql
        assert "total_violations" in sql

    def test_range_rule_uses_literal(self) -> None:
        rule = QualityRule(rule_type="range", field="age", config={"min": 0, "max": 120}, severity="ERROR", message="")
        sql = build_quality_check_sql([rule], "tfm_s1")
        assert sql is not None
        assert "age < 0" in sql
        assert "age > 120" in sql

    def test_regex_rule_escapes_pattern(self) -> None:
        rule = QualityRule(
            rule_type="regex",
            field="code",
            config={"pattern": "^[A-Z]+'"},
            severity="ERROR",
            message="",
        )
        sql = build_quality_check_sql([rule], "tfm_s1")
        assert sql is not None
        # Single quote in pattern should be doubled
        assert "''" in sql

    def test_no_rules_returns_none(self) -> None:
        sql = build_quality_check_sql([], "tfm_s1")
        assert sql is None

    def test_rejects_unsafe_field(self) -> None:
        rule = QualityRule(rule_type="not_null", field="bad; DROP", config={}, severity="ERROR", message="")
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            build_quality_check_sql([rule], "tfm_s1")
