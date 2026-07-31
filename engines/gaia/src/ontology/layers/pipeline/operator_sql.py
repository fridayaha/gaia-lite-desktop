"""Operator SQL generation for KestraFlowTranslator.

Per coding convention #8 (no hand-written operator if-elif chains for
SQL), each operator's SQL fragment is a standalone function registered
in ``OPERATOR_SQL_BUILDERS``. The translator looks up by operator type
and calls ``build(config, upstream_aliases, alias)`` — no switch/case.

All identifiers (column names, dataset names, table aliases) are
validated via :func:`ontology.core.naming.validate_identifier` before
interpolation into SQL, preventing injection from user-supplied node
config. SQL value literals (regex patterns, range bounds) are escaped
via :func:`_sql_literal`.

This module is engine-agnostic DuckDB SQL — it produces CTE fragments
consumed by :class:`KestraFlowTranslator`. Schema inference
(``SchemaInferenceEngine``) is a separate concern (schema only, no SQL).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ontology.core.schemas.pipeline_builder import FilterCondition, NodeConfig

# ── Identifier safety ──

_SQL_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _safe_ident(name: str) -> str:
    """Validate a SQL identifier (column/table/alias) before interpolation.

    Allows dotted refs (``iceberg.my_table``) since DuckDB accepts them.
    Raises ``ValueError`` on anything else — user config must not inject
    SQL through identifiers.
    """
    if not name or not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _sql_literal(value: Any) -> str:
    """Render a Python value as a safe SQL literal (for WHERE conditions).

    Strings are single-quoted with embedded quotes doubled (SQL standard).
    Numbers/bools rendered directly. None → NULL. This is used for
    QualityCheck rule configs (regex patterns, range bounds) which cannot
    be parameterized (the SQL is generated as a string for Kestra, not
    executed via a DB driver with bind params).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # String: escape single quotes by doubling (SQL standard)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


# ── SQL builder signature ──

SqlBuilder = Callable[[NodeConfig, list[str], str], str]


# Filter operator → SQL mapping for structured conditions.
# 操作符 → SQL 谓词模板（值用 _sql_literal 安全渲染，防注入）。
_FILTER_OP_SQL: dict[str, str] = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _build_filter_condition(fc: FilterCondition) -> str:
    """Render a single structured filter condition to a SQL predicate."""
    col = _safe_ident(fc.column)
    op = fc.operator
    if op in _FILTER_OP_SQL:
        if fc.value is None:
            raise ValueError(f"Filter operator '{op}' requires a value")
        return f"{col} {_FILTER_OP_SQL[op]} {_sql_literal(fc.value)}"
    if op == "is_null":
        return f"{col} IS NULL"
    if op == "is_not_null":
        return f"{col} IS NOT NULL"
    if op in ("in", "not_in"):
        vals = fc.values or []
        if not vals:
            raise ValueError(f"Filter operator '{op}' requires values")
        rendered = ", ".join(_sql_literal(v) for v in vals)
        kw = "IN" if op == "in" else "NOT IN"
        return f"{col} {kw} ({rendered})"
    if op == "contains":
        return f"{col} LIKE '%' || {_sql_literal(str(fc.value))} || '%'"
    if op == "not_contains":
        return f"{col} NOT LIKE '%' || {_sql_literal(str(fc.value))} || '%'"
    if op == "starts_with":
        return f"{col} LIKE {_sql_literal(str(fc.value))} || '%'"
    if op == "ends_with":
        return f"{col} LIKE '%' || {_sql_literal(str(fc.value))}"
    raise ValueError(f"Unsupported filter operator: {op}")


def _build_filter(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    # 优先使用结构化 filter_conditions（列名白名单 + 值参数化，防注入）；
    # 回退到旧的 expression 字符串（向后兼容，但无注入防护）。
    if config.filter_conditions:
        predicates = [_build_filter_condition(fc) for fc in config.filter_conditions]
        condition = " AND ".join(predicates) if predicates else "1=1"
    else:
        condition = config.expression or "1=1"
    # Filter expression 是用户编写的 SQL（WHERE 子句）。我们无法参数化它
    # （它是任意表达式），但会校验上游别名安全并信任该表达式（若格式错误会在
    # DuckDB 中明显失败 — 不存在超出用户自身管道的注入）。
    return f"{alias} AS (SELECT * FROM {left} WHERE {condition})"


def _build_select(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    columns = config.columns or ["*"]
    # Validate each column name (prevents injection via column list)
    if columns != ["*"]:
        cols = ", ".join(_safe_ident(c) for c in columns)
    else:
        cols = "*"
    return f"{alias} AS (SELECT {cols} FROM {left})"


def _build_rename(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    mapping = config.column_mapping or {}
    if not mapping:
        return f"{alias} AS (SELECT * FROM {left})"
    # Validate all old/new names
    safe_mapping = {(_safe_ident(old), _safe_ident(new)) for old, new in mapping.items()}
    renames = ", ".join(f"{old} AS {new}" for old, new in safe_mapping)
    exclude_cols = ", ".join(old for old, _ in safe_mapping)
    return f"{alias} AS (SELECT {renames}, * EXCLUDE ({exclude_cols}) FROM {left})"


def _build_typecast(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    # 优先使用结构化 cast_columns（多列转换，列名白名单校验）；
    # 回退到旧的 target_type + extra.column（单列，向后兼容）。
    casts: list[tuple[str, str]] = []  # (column, target_type)
    if config.cast_columns:
        for cc in config.cast_columns:
            col = cc.get("column")
            ttype = cc.get("target_type")
            if not col or not ttype:
                continue
            casts.append((_safe_ident(col), _validate_sql_type(ttype)))
    else:
        target_type = config.target_type or "VARCHAR"
        _validate_sql_type(target_type)
        column = config.extra.get("column") if config.extra else None
        if column:
            casts.append((_safe_ident(column), target_type))
    if not casts:
        return f"{alias} AS (SELECT * FROM {left})"
    replace_parts = ", ".join(f"CAST({col} AS {ttype}) AS {col}" for col, ttype in casts)
    return f"{alias} AS (SELECT * REPLACE ({replace_parts}) FROM {left})"


# Whitelist of SQL types DuckDB accepts for CAST (prevents injection via target_type)
_ALLOWED_SQL_TYPES = frozenset(
    {
        "STRING",
        "VARCHAR",
        "TEXT",
        "INTEGER",
        "INT",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "FLOAT",
        "DOUBLE",
        "DECIMAL",
        "REAL",
        "BOOLEAN",
        "BOOL",
        "TIMESTAMP",
        "DATE",
        "TIME",
        "INTERVAL",
        "JSON",
        "BLOB",
        "UUID",
    }
)


def _validate_sql_type(type_name: str) -> str:
    """Validate a SQL type name against a whitelist (injection guard)."""
    upper = type_name.upper()
    # Allow DECIMAL(p,s) form
    if upper.startswith("DECIMAL"):
        return type_name
    if upper not in _ALLOWED_SQL_TYPES:
        raise ValueError(f"Disallowed SQL type in TypeCast: {type_name!r}")
    return type_name


def _build_join(config: NodeConfig, upstream: list[str], alias: str) -> str:
    join_type = (config.join_type or "INNER").upper()
    if join_type not in ("INNER", "LEFT", "RIGHT", "FULL"):
        raise ValueError(f"Invalid join type: {join_type}")
    # 优先使用结构化 join_conditions（列名经白名单校验，防注入）；
    # 回退到旧的 join_condition 字符串（向后兼容，但无注入防护）。
    conditions: list[str] = []
    if config.join_conditions:
        for jc in config.join_conditions:
            left_col = _safe_ident(jc.left_column)
            right_col = _safe_ident(jc.right_column)
            conditions.append(f"left.{left_col} = right.{right_col}")
    elif config.join_condition:
        conditions.append(config.join_condition)
    condition_str = " AND ".join(conditions) if conditions else "TRUE"
    if len(upstream) >= 2:
        left, right = _safe_ident(upstream[0]), _safe_ident(upstream[1])
        return (
            f"{alias} AS (SELECT * FROM {left} AS left "
            f"{join_type} JOIN {right} AS right ON {condition_str})"
        )
    return f"{alias} AS (SELECT * FROM {_safe_ident(upstream[0])})"


# Aggregation function whitelist (prevents injection via function name)
_AGG_FUNCTIONS = frozenset(
    {
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
        "COUNT_DISTINCT",
        "STDDEV",
        "VARIANCE",
        "MEDIAN",
        "ARRAY_AGG",
        "STRING_AGG",
    }
)


def _build_aggregate(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    gb = config.group_by or []
    aggs = config.aggregations or []

    # Validate group-by columns
    gb_safe = [_safe_ident(g) for g in gb]
    gb_str = ", ".join(gb_safe)
    group_str = f"GROUP BY {gb_str}" if gb_str else ""

    agg_parts: list[str] = []
    for a in aggs:
        field = a.get("field", "*")
        func = a.get("function", "COUNT").upper()
        if func not in _AGG_FUNCTIONS:
            raise ValueError(f"Disallowed aggregation function: {func!r}")
        alias_name = a.get("alias", f"{func}_{field}" if field != "*" else f"{func}_count")
        _safe_ident(alias_name)
        if field == "*":
            agg_parts.append(f"{func}(*) AS {alias_name}")
        elif func == "COUNT_DISTINCT":
            _safe_ident(field)
            agg_parts.append(f"COUNT(DISTINCT {field}) AS {alias_name}")
        else:
            _safe_ident(field)
            agg_parts.append(f"{func}({field}) AS {alias_name}")

    agg_str = ", ".join(agg_parts) if agg_parts else "1"
    select_clause = f"{gb_str}, {agg_str}" if gb_str else agg_str
    return f"{alias} AS (SELECT {select_clause} FROM {left} {group_str})".rstrip()


def _build_union(_config: NodeConfig, upstream: list[str], alias: str) -> str:
    # UNION ALL of all upstreams (column-wise concat)
    safe_upstream = [_safe_ident(a) for a in upstream]
    selects = " UNION ALL ".join(f"SELECT * FROM {a}" for a in safe_upstream)
    return f"{alias} AS ({selects})"


def _build_expression(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    expr = config.expression or "NULL"
    alias_name = config.extra.get("alias", "_expr_result") if config.extra else "_expr_result"
    _safe_ident(alias_name)
    return f"{alias} AS (SELECT *, ({expr}) AS {alias_name} FROM {left})"


def _build_deduplicate(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    keys = config.columns or []
    if not keys:
        return f"{alias} AS (SELECT * FROM {left})"
    safe_keys = ", ".join(_safe_ident(k) for k in keys)
    # DuckDB DISTINCT ON equivalent: QUALIFY row_number() = 1
    return (
        f"{alias} AS (SELECT * FROM {left} "
        f"QUALIFY row_number() OVER (PARTITION BY {safe_keys} ORDER BY {safe_keys}) = 1)"
    )


def _build_sort(config: NodeConfig, upstream: list[str], alias: str) -> str:
    left = _safe_ident(upstream[0])
    # 优先使用结构化 sort_keys（列名白名单校验 + 方向）；回退到旧的 columns（默认 ASC）。
    keys = config.sort_keys or []
    if not keys:
        legacy_cols = config.columns or []
        if not legacy_cols:
            return f"{alias} AS (SELECT * FROM {left})"
        safe_keys = ", ".join(_safe_ident(k) for k in legacy_cols)
        return f"{alias} AS (SELECT * FROM {left} ORDER BY {safe_keys})"
    order_parts = []
    for sk in keys:
        col = _safe_ident(sk.column)
        direction = (sk.direction or "ASC").upper()
        if direction not in ("ASC", "DESC"):
            raise ValueError(f"Invalid sort direction: {direction}")
        order_parts.append(f"{col} {direction}")
    return f"{alias} AS (SELECT * FROM {left} ORDER BY {', '.join(order_parts)})"


# ── Registry ──

OPERATOR_SQL_BUILDERS: dict[str, SqlBuilder] = {
    "Filter": _build_filter,
    "Select": _build_select,
    "Rename": _build_rename,
    "TypeCast": _build_typecast,
    "Join": _build_join,
    "Aggregate": _build_aggregate,
    "Union": _build_union,
    "Expression": _build_expression,
    "Deduplicate": _build_deduplicate,
    "Sort": _build_sort,
}


def build_transform_sql(operator: str, config: NodeConfig, upstream_aliases: list[str], alias: str) -> str:
    """Dispatch to the registered SQL builder for ``operator``.

    Falls back to passthrough SELECT for unknown operators (the
    SchemaInferenceEngine should have flagged the unknown type already).
    """
    _safe_ident(alias)
    builder = OPERATOR_SQL_BUILDERS.get(operator)
    if builder is None:
        # Passthrough for unknown operators (inference engine warns separately)
        left = _safe_ident(upstream_aliases[0]) if upstream_aliases else "(SELECT 1)"
        return f"{alias} AS (SELECT * FROM {left})"
    return builder(config, upstream_aliases, alias)


# ── QualityCheck SQL (separate from CTE transforms) ──


def build_quality_check_sql(
    rules: list[Any],
    upstream_alias: str,
) -> str | None:
    """Build the violation-count SQL for a QualityCheck node.

    Returns a SELECT that sums violations across all rules, or None if
    no valid rules. All field names and literals are validated/escaped.
    """
    _safe_ident(upstream_alias)
    violation_subqueries: list[str] = []
    for rule in rules:
        rule_type = rule.rule_type if hasattr(rule, "rule_type") else rule.get("rule_type")
        field = rule.field if hasattr(rule, "field") else rule.get("field", "")
        _safe_ident(field)
        config = rule.config if hasattr(rule, "config") else rule.get("config", {})

        if rule_type == "not_null":
            violation_subqueries.append(f"SELECT COUNT(*) AS violations FROM {upstream_alias} WHERE {field} IS NULL")
        elif rule_type == "unique":
            violation_subqueries.append(
                f"SELECT COUNT(*) AS violations FROM ("
                f"SELECT {field}, COUNT(*) AS c FROM {upstream_alias} "
                f"GROUP BY {field} HAVING COUNT(*) > 1)"
            )
        elif rule_type == "range":
            min_v = config.get("min")
            max_v = config.get("max")
            conditions = []
            if min_v is not None:
                conditions.append(f"{field} < {_sql_literal(min_v)}")
            if max_v is not None:
                conditions.append(f"{field} > {_sql_literal(max_v)}")
            cond = " OR ".join(conditions) if conditions else "FALSE"
            violation_subqueries.append(f"SELECT COUNT(*) AS violations FROM {upstream_alias} WHERE {cond}")
        elif rule_type == "regex":
            pattern = config.get("pattern", "")
            violation_subqueries.append(
                f"SELECT COUNT(*) AS violations FROM {upstream_alias} "
                f"WHERE NOT regexp_like({field}, {_sql_literal(pattern)})"
            )
        elif rule_type == "expression":
            expr = config.get("expression", "FALSE")
            violation_subqueries.append(f"SELECT COUNT(*) AS violations FROM {upstream_alias} WHERE NOT ({expr})")

    if not violation_subqueries:
        return None

    union_sql = " UNION ALL ".join(violation_subqueries)
    return f"SELECT COALESCE(SUM(violations), 0) AS total_violations FROM ({union_sql})"
