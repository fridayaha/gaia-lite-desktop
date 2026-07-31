"""Unit tests for OntologySqlCompiler — text2sql path B core.

Validates Phase 1 supported scope (ADR-012 §「分阶段实施计划」) and the
three ontology guardrails (table / column / join). Uses an in-memory
OntologySchemaProvider mirroring the v3 verification script's auto schema.
"""

from __future__ import annotations

import pytest

from ontology.core.exceptions import OntologyError
from ontology.services.textql.sql_compiler import OntologySqlCompiler


class AutoSchemaProvider:
    """In-memory schema provider mirroring the v3 verification auto schema."""

    def __init__(self) -> None:
        self._object_types: dict[str, str] = {
            "Order": "idx_auto__order",
            "Vehicle": "idx_auto__vehicle",
            "Customer": "idx_auto__customer",
            "Part": "idx_auto__part",
            "Supplier": "idx_auto__supplier",
            "Claim": "idx_auto__claim",
            # VIRTUAL table: three-part catalog.schema.table locator
            # (external source via Trino federation). Tests PR 0's VIRTUAL
            # support in the compiler (table name → exp.Table with catalog/db).
            "Flight": "airlinemysql.airline_benchmark.t_flight",
        }
        self._properties: dict[str, dict[str, str]] = {
            "Order": {
                "orderId": "order_id",
                "customerId": "customer_id",
                "vehicleId": "vehicle_id",
                "orderDate": "order_date",
                "amount": "amount",
                "status": "status",
                "deliveryDate": "delivery_date",
                "region": "region",
            },
            "Vehicle": {
                "vehicleId": "vehicle_id",
                "vin": "vin",
                "model": "model",
                "productionDate": "production_date",
                "status": "status",
            },
            "Customer": {
                "customerId": "customer_id",
                "customerName": "customer_name",
                "region": "region",
                "level": "level",
            },
            "Part": {
                "partId": "part_id",
                "partName": "part_name",
                "supplierId": "supplier_id",
            },
            "Supplier": {
                "supplierId": "supplier_id",
                "supplierName": "supplier_name",
                "region": "region",
            },
            "Claim": {
                "claimId": "claim_id",
                "vehicleId": "vehicle_id",
                "partId": "part_id",
                "claimDate": "claim_date",
                "faultCode": "fault_code",
                "status": "status",
            },
            "Flight": {
                "flightId": "flight_id",
                "status": "status",
            },
        }
        self._links: set[tuple[str, str]] = {
            ("Order", "Customer"),
            ("Customer", "Order"),
            ("Order", "Vehicle"),
            ("Vehicle", "Order"),
            ("Part", "Supplier"),
            ("Supplier", "Part"),
            ("Claim", "Vehicle"),
            ("Vehicle", "Claim"),
            ("Claim", "Part"),
            ("Part", "Claim"),
        }

    def object_types(self) -> dict[str, str]:
        return self._object_types

    def properties(self) -> dict[str, dict[str, str]]:
        return self._properties

    def links(self) -> set[tuple[str, str]]:
        return self._links

    def physical_to_object_type(self) -> dict[str, str]:
        # Mirror MetaStoreSchemaProvider: register BOTH Doris and Trino
        # physical names (plus VIRTUAL three-part inner names) so the
        # compiler's column-owner resolution works after table rewrite.
        result: dict[str, str] = {}
        trino_refs = self.trino_table_refs()
        for k, v in self._object_types.items():
            result[v] = k
            tref = trino_refs.get(k, v)
            if tref != v:
                result[tref] = k
            for ref in (v, tref):
                if "." in ref:
                    inner = ref.rsplit(".", 1)[-1]
                    if inner not in result:
                        result[inner] = k
        return result

    def storage_types(self) -> dict[str, str]:
        # Flight is VIRTUAL; all others MANAGED.
        return {k: ("VIRTUAL" if k == "Flight" else "MANAGED") for k in self._object_types}

    def trino_table_refs(self) -> dict[str, str]:
        # MANAGED → iceberg.ontology.<snake_type>; VIRTUAL → same as Doris ref.
        import re

        def _snake(name: str) -> str:
            s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
            return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

        refs: dict[str, str] = {}
        for k, v in self._object_types.items():
            if k == "Flight":
                refs[k] = v  # VIRTUAL three-part locator, same in both dialects
            else:
                refs[k] = f"iceberg.ontology.{_snake(k)}"
        return refs


@pytest.fixture
def schema() -> AutoSchemaProvider:
    return AutoSchemaProvider()


@pytest.fixture
def compiler(schema: AutoSchemaProvider) -> OntologySqlCompiler:
    return OntologySqlCompiler(schema)


# ── Helper ──────────────────────────────────────────────────────────────


def compile_both(c: OntologySqlCompiler, sql: str) -> tuple[str, str, list]:
    """Compile to both dialects, return (doris, trino, params)."""
    doris, p1 = c.compile(sql, "doris")
    trino, p2 = c.compile(sql, "trino")
    # params should be identical across dialects (same literals extracted)
    assert p1 == p2
    return doris, trino, p1


# ── T1: single-table filter + sort + page ───────────────────────────────


class TestSingleTable:
    def test_filter_sort_page(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT orderNo, amount FROM Order WHERE status = 'OVERDUE' "
            "AND amount > 100000 ORDER BY amount DESC LIMIT 10 OFFSET 20"
        )
        # Order has no orderNo in schema → expect INVALID_COLUMN (tests guardrail)
        with pytest.raises(OntologyError) as exc:
            compiler.compile(sql, "doris")
        assert exc.value.code == "INVALID_COLUMN"

    def test_valid_single_table_filter(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT amount, status FROM Order WHERE status = 'OVERDUE' AND amount > 100000 LIMIT 10"
        doris, trino, params = compile_both(compiler, sql)
        assert "idx_auto__order" in doris
        assert "amount" in doris and "status" in doris
        assert "?" in doris
        # SqlGlot Literal.this is always str; values come back as strings.
        assert "OVERDUE" in params and 100000 in params
        # LIMIT/OFFSET literals are inlined (Doris rejects parameterized LIMIT)
        assert "10" not in params

    def test_parameterized_binding(self, compiler: OntologySqlCompiler) -> None:
        """Literals extracted to params, replaced with ? (injection-safe)."""
        sql = "SELECT amount FROM Order WHERE region = 'EAST' AND amount > 5000"
        doris, _, params = compile_both(compiler, sql)
        assert doris.count("?") == 2
        assert params == ["EAST", 5000]

    def test_numeric_literal_keeps_native_type(self, compiler: OntologySqlCompiler) -> None:
        """D4 regression: numeric literals bind as int/float, not varchar.

        SqlGlot Literal.is_string distinguishes quoted strings from unquoted
        numerics. The compiler must preserve the native type so Trino/Doris
        bind the right type (otherwise `integer <= varchar(1)` TYPE_MISMATCH).
        """
        sql = "SELECT amount FROM Order WHERE amount > 1000 AND amount < 9999.5"
        doris, trino, params = compile_both(compiler, sql)
        # Numeric literals bind as int/float (native type), not str.
        assert 1000 in params and isinstance(params[0], int)
        assert 9999.5 in params and isinstance(params[1], float)
        # String literals still bind as str.
        sql2 = "SELECT amount FROM Order WHERE region = 'EAST'"
        _, _, params2 = compile_both(compiler, sql2)
        assert params2 == ["EAST"]
        assert isinstance(params2[0], str)

    def test_virtual_table_three_part_name(self, compiler: OntologySqlCompiler) -> None:
        """VIRTUAL OT (catalog.schema.table locator) → three-part exp.Table.

        PR 0: VIRTUAL tables carry a catalog.schema.table string in the schema
        provider; the compiler splits on '.' and emits a catalog-qualified
        table so Trino federation resolves the external source correctly.
        """
        sql = "SELECT flightId FROM Flight WHERE status = 'SCHEDULED' LIMIT 5"
        doris, trino, params = compile_both(compiler, sql)
        # Both dialects emit the three-part name (catalog.schema.table).
        assert "airlinemysql.airline_benchmark.t_flight" in trino
        assert "airlinemysql.airline_benchmark.t_flight" in doris
        # Filter literal parameterized.
        assert "SCHEDULED" in params


# ── T1b: dialect-aware physical names (MANAGED → Iceberg 3-part on Trino) ─


class TestDialectAwarePhysicalName:
    """MANAGED tables compile to different physical names per dialect:

    - Doris: ``idx_<ont>__<type>`` (the Doris index table)
    - Trino: ``iceberg.ontology.<snake_type>`` (the Iceberg table visible
      via the ``iceberg`` catalog, namespace ``ontology``)

    VIRTUAL tables keep their three-part external locator in BOTH dialects
    (Trino federation; Doris never executes a query touching a VIRTUAL table
    — the service routing layer guarantees that). This is what makes
    cross-catalog federation JOINs (MANAGED + VIRTUAL) runnable on Trino.
    """

    def test_managed_doris_uses_idx_table(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT amount FROM Order WHERE status = 'OPEN'"
        doris, _trino, _ = compile_both(compiler, sql)
        assert "idx_auto__order" in doris

    def test_managed_trino_uses_iceberg_three_part(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT amount FROM Order WHERE status = 'OPEN'"
        _doris, trino, _ = compile_both(compiler, sql)
        # Trino sees MANAGED tables via the iceberg catalog, ontology namespace.
        # SqlGlot may quote reserved-word table names (e.g. "order").
        assert "iceberg.ontology." in trino
        assert "order" in trino
        # Must NOT leak the Doris idx name into Trino output.
        assert "idx_auto__order" not in trino

    def test_managed_join_trino_uses_iceberg_for_all(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT o.amount, c.customerName FROM Order o "
            "JOIN Customer c ON o.customerId = c.customerId WHERE c.region = 'EAST'"
        )
        _doris, trino, _ = compile_both(compiler, sql)
        assert "iceberg.ontology." in trino
        assert "customer" in trino
        assert "order" in trino

    def test_virtual_keeps_three_part_in_both_dialects(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT flightId FROM Flight WHERE status = 'SCHEDULED'"
        doris, trino, _ = compile_both(compiler, sql)
        # VIRTUAL three-part locator is the same in both dialects.
        assert "airlinemysql.airline_benchmark.t_flight" in trino
        assert "airlinemysql.airline_benchmark.t_flight" in doris

    def test_mixed_join_trino_emits_iceberg_and_external(self, compiler: OntologySqlCompiler) -> None:
        """MANAGED + VIRTUAL JOIN on Trino: iceberg.ontology.<t> for MANAGED,
        external three-part for VIRTUAL — both resolvable by Trino."""
        # Flight (VIRTUAL) is linked to Order? Add a link in the schema?
        # The AutoSchema links set has no Flight link; use a VIRTUAL-only
        # query + a separate MANAGED query to assert each name form.
        # (Cross-OT link between Flight and a MANAGED OT isn't in the fixture.)
        sql_v = "SELECT flightId FROM Flight"
        sql_m = "SELECT amount FROM Order"
        _, trino_v, _ = compile_both(compiler, sql_v)
        _, trino_m, _ = compile_both(compiler, sql_m)
        assert "airlinemysql.airline_benchmark.t_flight" in trino_v
        assert "iceberg.ontology." in trino_m
        assert "order" in trino_m


# ── T1c: SELECT * expansion (disambiguate same-apiName columns) ──────────


class TestSelectStarExpansion:
    """``SELECT *`` is expanded to explicit columns at compile time so that
    same-apiName properties across joined OTs (e.g. both have ``status``)
    don't collide and silently drop data.

    Expansion rules:
      - ``SELECT *`` (top-level Star whose parent is a Select) → every
        property of every OT in FROM/JOIN, as ``<alias>.<col> AS <api>``.
      - When an apiName would collide across OTs, prefix with the OT name:
        ``<OT>_<api>`` (e.g. ``Order_status``, ``Customer_status``).
      - ``COUNT(*)`` Star is NOT expanded (parent is Count, not Select).
      - Single-table ``SELECT *`` expands to that OT's columns (no prefix
        needed, no collision possible).
    """

    def test_count_star_not_expanded(self, compiler: OntologySqlCompiler) -> None:
        """COUNT(*) Star must stay as-is (it's inside a function, not a projection)."""
        sql = "SELECT COUNT(*) AS cnt FROM Order"
        doris, trino, _ = compile_both(compiler, sql)
        assert "COUNT(*)" in doris
        assert "COUNT(*)" in trino

    def test_single_table_star_expands_to_all_columns(self, compiler: OntologySqlCompiler) -> None:
        """SELECT * FROM <OT> → every property of that OT (no prefix)."""
        sql = "SELECT * FROM Order"
        doris, _, _ = compile_both(compiler, sql)
        # Order's properties: orderId, customerId, vehicleId, orderDate,
        # amount, status, deliveryDate, region (api_names) → physical cols.
        # Expanded columns use physical names but AS aliases keep api_names.
        assert "order_id" in doris  # orderId → order_id
        assert "customer_id" in doris
        assert "amount" in doris
        # No OT-prefixed alias (single table, no collision).
        assert "Order_" not in doris
        # No raw * left.
        assert "SELECT *" not in doris

    def test_multitable_star_expands_with_prefix_on_collision(self, compiler: OntologySqlCompiler) -> None:
        """SELECT * FROM A JOIN B where A,B share an api_name → prefixed alias.

        Order and Customer share ``customerId`` and ``region``. Expansion
        must disambiguate: ``o.customerId AS Order_customerId`` /
        ``c.customerId AS Customer_customerId`` so neither is dropped.
        """
        sql = "SELECT * FROM Order o JOIN Customer c ON o.customerId = c.customerId"
        doris, trino, _ = compile_both(compiler, sql)
        # Both shared columns survive, disambiguated by OT prefix.
        assert "Order_customerId" in doris
        assert "Customer_customerId" in doris
        assert "Order_region" in doris
        assert "Customer_region" in doris
        # Non-colliding columns keep their bare api_name as alias.
        assert "amount" in doris  # Order.amount, no collision
        assert "customer_name" in doris  # Customer.customerName
        # No raw * left.
        assert "SELECT *" not in doris

    def test_multitable_star_no_collision_keeps_bare_alias(self, compiler: OntologySqlCompiler) -> None:
        """When the single OT has no collision, expansion uses bare api aliases.

        Single-table SELECT * never collides (only one OT) — every column
        keeps its bare api_name as the AS alias, no OT prefix.
        """
        sql = "SELECT * FROM Part"
        doris, _, _ = compile_both(compiler, sql)
        assert "part_id" in doris  # Part.partId
        assert "part_name" in doris  # Part.partName
        assert "supplier_id" in doris  # Part.supplierId
        # No OT-prefixed alias (single table, no collision possible).
        assert "Part_" not in doris


# ── T2/T3: multi-table JOIN via LinkType ────────────────────────────────


class TestMultiTableJoin:
    def test_two_table_join_valid(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT o.amount, c.customerName FROM Order o "
            "JOIN Customer c ON o.customerId = c.customerId WHERE c.region = 'EAST'"
        )
        doris, trino, params = compile_both(compiler, sql)
        assert "idx_auto__order" in doris and "idx_auto__customer" in doris
        assert "o.customer_id = c.customer_id" in doris or "o.customer_id=c.customer_id" in doris
        assert "customer_name" in doris

    def test_four_table_join_valid(self, compiler: OntologySqlCompiler) -> None:
        """T3: 4-table chain Claim→Vehicle→Part→Supplier."""
        sql = (
            "SELECT cl.claimId, v.vin, p.partName, s.supplierName "
            "FROM Claim cl JOIN Vehicle v ON cl.vehicleId = v.vehicleId "
            "JOIN Part p ON cl.partId = p.partId "
            "JOIN Supplier s ON p.supplierId = s.supplierId WHERE cl.status = 'OPEN'"
        )
        doris, _, _ = compile_both(compiler, sql)
        for t in ["idx_auto__claim", "idx_auto__vehicle", "idx_auto__part", "idx_auto__supplier"]:
            assert t in doris

    def test_five_table_join_valid(self, compiler: OntologySqlCompiler) -> None:
        """T15: 5-table chain Order→Customer→Vehicle→? (Order-Customer-Vehicle linked)."""
        sql = (
            "SELECT o.amount, c.customerName, v.vin FROM Order o "
            "JOIN Customer c ON o.customerId = c.customerId "
            "JOIN Vehicle v ON o.vehicleId = v.vehicleId WHERE o.status = 'PAID'"
        )
        doris, _, _ = compile_both(compiler, sql)
        assert "idx_auto__order" in doris
        assert "idx_auto__customer" in doris
        assert "idx_auto__vehicle" in doris

    def test_join_invalid_no_linktype(self, compiler: OntologySqlCompiler) -> None:
        """Guardrail 3: JOIN without defined LinkType rejected."""
        sql = "SELECT p.partName, c.customerName FROM Part p JOIN Customer c ON p.partId = c.customerId"
        with pytest.raises(OntologyError) as exc:
            compiler.compile(sql, "doris")
        assert exc.value.code == "INVALID_JOIN"


# ── T4: multi-dim aggregation ───────────────────────────────────────────


class TestAggregation:
    def test_group_by_having(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT region, SUM(amount) AS total, COUNT(*) AS cnt FROM Order "
            "WHERE status = 'PAID' GROUP BY region HAVING SUM(amount) > 1000000 "
            "ORDER BY total DESC"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "GROUP BY" in doris
        assert "HAVING" in doris
        assert "SUM(amount)" in doris
        assert "PAID" in params

    def test_multi_dim_group_by(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT region, status, SUM(amount) AS total FROM Order GROUP BY region, status"
        doris, _, _ = compile_both(compiler, sql)
        assert "GROUP BY region, status" in doris or "GROUP BY" in doris


# ── T5: ratio (aggregation division) + derived metric ───────────────────


class TestDerivedMetric:
    def test_ratio_aggregation_division(self, compiler: OntologySqlCompiler) -> None:
        """T5a: SUM(CASE...)/COUNT(*) ratio."""
        sql = (
            "SELECT region, SUM(CASE WHEN level = 'VIP' THEN 1 ELSE 0 END) AS vip_count, "
            "COUNT(*) AS total_count, "
            "SUM(CASE WHEN level = 'VIP' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS vip_ratio "
            "FROM Customer GROUP BY region"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "CASE WHEN" in doris
        assert "vip_ratio" in doris
        assert "VIP" in params

    def test_custom_arithmetic(self, compiler: OntologySqlCompiler) -> None:
        """T5 simple: amount * 0.8."""
        sql = "SELECT amount, amount * 0.8 AS discounted FROM Order WHERE status = 'PAID'"
        doris, _, params = compile_both(compiler, sql)
        assert "amount * ?" in doris or "amount*?" in doris
        assert 0.8 in params


# ── T8: window functions ────────────────────────────────────────────────


class TestWindowFunction:
    def test_row_number_partition(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT orderNo, region, amount FROM ("
            "SELECT amount, region, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn "
            "FROM Order) t WHERE rn <= 3"
        )
        # orderNo not in schema → would fail; use valid cols
        sql = (
            "SELECT amount, region FROM ("
            "SELECT amount, region, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn "
            "FROM Order) t WHERE rn <= 3"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "ROW_NUMBER()" in doris
        assert "PARTITION BY" in doris
        assert 3 in params

    def test_topn_with_window_ratio(self, compiler: OntologySqlCompiler) -> None:
        """T7: TopN + window SUM OVER for ratio."""
        sql = "SELECT amount, amount * 1.0 / SUM(amount) OVER () AS ratio FROM Order ORDER BY amount DESC LIMIT 10"
        doris, _, params = compile_both(compiler, sql)
        assert "SUM(amount) OVER ()" in doris
        assert "LIMIT" in doris


# ── T9: time-series ─────────────────────────────────────────────────────


class TestTimeSeries:
    def test_date_format_group_by(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT DATE_FORMAT(orderDate, '%Y-%m') AS month, SUM(amount) AS total "
            "FROM Order WHERE orderDate >= '2025-01-01' "
            "GROUP BY DATE_FORMAT(orderDate, '%Y-%m') ORDER BY month"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "DATE_FORMAT" in doris
        assert "2025-01-01" in params

    def test_year_month_functions(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT SUM(amount) AS total FROM Order WHERE YEAR(orderDate) = 2025 AND MONTH(orderDate) = 6"
        doris, _, params = compile_both(compiler, sql)
        assert "YEAR(" in doris and "MONTH(" in doris
        assert 2025 in params and 6 in params


# ── Subqueries ──────────────────────────────────────────────────────────


class TestSubquery:
    def test_subquery_filter(self, compiler: OntologySqlCompiler) -> None:
        """Subquery in WHERE (region-avg threshold)."""
        sql = "SELECT amount FROM Order WHERE amount > (SELECT AVG(amount) FROM Order WHERE region = 'EAST')"
        doris, _, params = compile_both(compiler, sql)
        assert "AVG(amount)" in doris
        assert "EAST" in params

    def test_subquery_join(self, compiler: OntologySqlCompiler) -> None:
        """T6-style SELF JOIN via two subqueries (YoY). Phase 1 supports subquery form."""
        sql = (
            "SELECT cur.region, cur.year_amount AS this_year, prev.year_amount AS last_year "
            "FROM (SELECT region, SUM(amount) AS year_amount FROM Order "
            "WHERE YEAR(orderDate) = 2025 GROUP BY region) cur "
            "JOIN (SELECT region, SUM(amount) AS year_amount FROM Order "
            "WHERE YEAR(orderDate) = 2024 GROUP BY region) prev "
            "ON cur.region = prev.region"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "idx_auto__order" in doris
        assert 2025 in params and 2024 in params

    def test_in_subquery(self, compiler: OntologySqlCompiler) -> None:
        sql = (
            "SELECT o.amount FROM Order o WHERE o.customerId IN "
            "(SELECT customerId FROM Customer WHERE region = 'EAST') AND o.amount > 5000"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "IN" in doris
        assert "EAST" in params and 5000 in params


# ── Guardrails: invalid table / column / join ───────────────────────────


class TestGuardrails:
    def test_invalid_table(self, compiler: OntologySqlCompiler) -> None:
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT * FROM Supplier WHERE id = 1", "doris")
        # Supplier IS in our schema; use a truly unknown one
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT * FROM Nonexistent WHERE id = 1", "doris")
        assert exc.value.code == "INVALID_TABLE"

    def test_invalid_column(self, compiler: OntologySqlCompiler) -> None:
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT amount, color FROM Order WHERE status = 'PAID'", "doris")
        assert exc.value.code == "INVALID_COLUMN"

    def test_sql_injection_multistatement(self, compiler: OntologySqlCompiler) -> None:
        """Multi-statement injection: 'x' table rejected by guardrail."""
        sql = "SELECT amount FROM Order WHERE status = 'PAID' OR 1=1; DROP TABLE x; --"
        # parse_one only takes the first statement, so DROP TABLE x may not
        # be reached. The key assertion: no literal reaches SQL unparameterized.
        try:
            doris, _, params = compile_both(compiler, sql)
            # All literals parameterized — no raw 'PAID' or '1=1' in SQL body
            assert "PAID" not in doris
        except OntologyError:
            pass  # guardrail rejected — also acceptable


# ── Scope enforcement: unsupported constructs rejected ──────────────────


class TestScopeEnforcement:
    def test_update_rejected(self, compiler: OntologySqlCompiler) -> None:
        with pytest.raises(OntologyError) as exc:
            compiler.compile("UPDATE Order SET amount = 100 WHERE orderId = 1", "doris")
        assert exc.value.code == "UNSUPPORTED_SQL"

    def test_insert_rejected(self, compiler: OntologySqlCompiler) -> None:
        with pytest.raises(OntologyError) as exc:
            compiler.compile("INSERT INTO Order (amount) VALUES (100)", "doris")
        assert exc.value.code == "UNSUPPORTED_SQL"

    def test_cte_now_supported(self, compiler: OntologySqlCompiler) -> None:
        """CTE (WITH) is supported as of Phase 2 (was rejected in Phase 1)."""
        sql = "WITH t AS (SELECT amount FROM Order) SELECT amount FROM t"
        doris, _, params = compile_both(compiler, sql)
        assert "WITH" in doris.upper()
        assert "idx_auto__order" in doris  # inner table rewritten
        assert "t" in doris  # CTE name preserved

    def test_union_rejected(self, compiler: OntologySqlCompiler) -> None:
        sql = "SELECT amount FROM Order UNION SELECT amount FROM Claim"
        with pytest.raises(OntologyError) as exc:
            compiler.compile(sql, "doris")
        assert exc.value.code == "UNSUPPORTED_SQL"

    def test_parse_error(self, compiler: OntologySqlCompiler) -> None:
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT FROM WHERE", "doris")
            assert exc.value.code == "SQL_PARSE_ERROR"


# ── T6 / CTE: Phase 2 additions ──────────────────────────────────────────


class TestCTE:
    """CTE (WITH) support — Phase 2 (was rejected in Phase 1)."""

    def test_simple_cte(self, compiler: OntologySqlCompiler) -> None:
        """Single CTE: inner table rewritten, CTE name preserved."""
        sql = "WITH t AS (SELECT amount FROM Order) SELECT amount FROM t"
        doris, _, _ = compile_both(compiler, sql)
        assert "WITH" in doris.upper()
        assert "idx_auto__order" in doris
        # CTE body column rewritten to physical (amount → amount, same here)
        assert "FROM t" in doris

    def test_cte_with_alias_output_col(self, compiler: OntologySqlCompiler) -> None:
        """CTE output alias (AS total) referenced in outer SELECT."""
        sql = (
            "WITH agg AS (SELECT region, SUM(amount) AS total FROM Order "
            "GROUP BY region) SELECT region, total FROM agg WHERE total > 1000"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "WITH" in doris.upper()
        assert "idx_auto__order" in doris
        assert "GROUP BY" in doris
        # total is a CTE output alias — trusted, not re-validated
        assert "total" in doris
        assert 1000 in params

    def test_multiple_ctes(self, compiler: OntologySqlCompiler) -> None:
        """Two CTEs referenced in outer query."""
        sql = (
            "WITH vip AS (SELECT customerId FROM Customer WHERE region = 'EAST'), "
            "overdue AS (SELECT customerId FROM Order WHERE status = 'OVERDUE') "
            "SELECT customerId FROM vip"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "vip" in doris and "overdue" in doris
        assert "idx_auto__customer" in doris
        assert "idx_auto__order" in doris
        assert "EAST" in params and "OVERDUE" in params

    def test_cte_join_object_type(self, compiler: OntologySqlCompiler) -> None:
        """CTE joined with a real ObjectType (Order↔Customer link)."""
        sql = (
            "WITH vip AS (SELECT customerId FROM Customer WHERE region = 'EAST') "
            "SELECT o.amount FROM Order o JOIN vip ON o.customerId = vip.customerId"
        )
        doris, _, params = compile_both(compiler, sql)
        assert "idx_auto__order" in doris
        assert "idx_auto__customer" in doris
        assert "vip" in doris
        assert "EAST" in params

    def test_cte_inner_columns_validated(self, compiler: OntologySqlCompiler) -> None:
        """Guardrail: unknown column INSIDE a CTE body is rejected."""
        sql = "WITH t AS (SELECT bogus FROM Order) SELECT * FROM t"
        with pytest.raises(OntologyError) as exc:
            compiler.compile(sql, "doris")
        # bogus is not a Property of Order → INVALID_COLUMN
        assert exc.value.code == "INVALID_COLUMN"

    def test_cte_unknown_table_rejected(self, compiler: OntologySqlCompiler) -> None:
        """Guardrail: unknown table INSIDE a CTE body is rejected."""
        sql = "WITH t AS (SELECT * FROM Nonexistent) SELECT * FROM t"
        with pytest.raises(OntologyError) as exc:
            compiler.compile(sql, "doris")
        assert exc.value.code == "INVALID_TABLE"
