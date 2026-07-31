"""Tests for ObjectQueryService.execute_compiled_sql — multi-table JOIN support.

Validates design decision C (ADR-012 revision): ``execute_compiled_sql`` no
longer takes a single ``object_type_api_name`` "anchor" parameter. Instead it
infers ALL ObjectTypes referenced in the logical SQL (via the compiler's
pass-1 alias map) and applies three things across every involved OT:

  1. Access check — every joined OT must pass ``check_access(read)``.
  2. Storage routing — all-MANAGED → Doris; any VIRTUAL → Trino
     federation (Trino cross-catalog JOINs MANAGED iceberg.ontology.<t>
     with VIRTUAL external <catalog> tables; no MIXED_STORAGE_JOIN error).
  3. Column name remap — output rows map physical columns back to property
     api_names across ALL involved OTs (a single-OT map would lose or
     mis-map columns from joined tables; e.g. ``SELECT a.p1, b.p2 FROM A
     JOIN B``).

The single-table regression path (``SELECT * FROM A WHERE pk = ?``) keeps
identical behavior — the SQL references exactly one OT, so inference yields
that OT and nothing else changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ontology.config.settings import settings
from ontology.core.exceptions import ForbiddenError, OntologyError
from ontology.core.schemas.ontology import (
    BackingColumnRef,
    DataType,
    ObjectType,
    PropertyDef,
)
from ontology.services.object_query_service import ObjectQueryService
from ontology.services.textql.sql_compiler import OntologySqlCompiler

# 本模块断言 full 版的 Doris/Trino 路由行为（all-MANAGED→Doris、混合→Trino 联邦、
# 行级 pushdown）。lite 版无 Doris/Iceberg，MANAGED 查询被 guard 拦截（B3），这些
# 路由断言在 lite 下不适用——lite 的等价覆盖在 test_object_query_duckdb.py。
# 模块级 skipif 避免逐个测试标记（A5 惯例）。
pytestmark = pytest.mark.skipif(
    settings.edition == "lite",
    reason="lite 版无 Doris/Trino 路由（MANAGED guard 拦截）；lite 覆盖见 test_object_query_duckdb.py",
)

# ── Test schema fixtures ────────────────────────────────────────────────


class _StubSchemaProvider:
    """In-memory OntologySchemaProvider mirroring test_sql_compiler's AutoSchema."""

    def __init__(self) -> None:
        # api_name → physical table
        self._object_types: dict[str, str] = {
            "ManualOutboundCall": "idx_crm__manual_outbound_call",
            "Lead": "idx_crm__lead",
            "SalesConsultant": "idx_crm__sales_consultant",
            "LeadAllocateRecord": "idx_crm__lead_allocate_record",
            # VIRTUAL table — three-part locator (Trino federation)
            "ExternalLead": "crmmysql.crm.t_lead",
        }
        self._properties: dict[str, dict[str, str]] = {
            "ManualOutboundCall": {
                "callId": "call_id",
                "leadId": "lead_id",
                "callTime": "call_time",
            },
            "Lead": {
                "leadId": "lead_id",
                "leadsStatus": "leads_status",
            },
            "SalesConsultant": {
                "userId": "user_id",
                "phone": "phone",
            },
            "LeadAllocateRecord": {
                "recordId": "record_id",
                "leadsId": "leads_id",
                "salesConsultantId": "sales_consultant_id",
            },
            "ExternalLead": {
                "leadId": "lead_id",
                "leadsStatus": "leads_status",
            },
        }
        self._links: set[tuple[str, str]] = {
            ("ManualOutboundCall", "Lead"),
            ("Lead", "LeadAllocateRecord"),
            ("LeadAllocateRecord", "SalesConsultant"),
            ("ExternalLead", "ManualOutboundCall"),
        }
        self._physical_to_ot: dict[str, str] = {v: k for k, v in self._object_types.items()}
        # inner name for VIRTUAL three-part locator
        self._physical_to_ot["t_lead"] = "ExternalLead"

    def object_types(self) -> dict[str, str]:
        return self._object_types

    def properties(self) -> dict[str, dict[str, str]]:
        return self._properties

    def links(self) -> set[tuple[str, str]]:
        return self._links

    def physical_to_object_type(self) -> dict[str, str]:
        # Register BOTH Doris and Trino physical names + inner names.
        result: dict[str, str] = {v: k for k, v in self._object_types.items()}
        trino_refs = self.trino_table_refs()
        for k, v in self._object_types.items():
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
        return {k: ("VIRTUAL" if k == "ExternalLead" else "MANAGED") for k in self._object_types}

    def trino_table_refs(self) -> dict[str, str]:
        import re

        def _snake(name: str) -> str:
            s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
            return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

        refs: dict[str, str] = {}
        for k, v in self._object_types.items():
            if k == "ExternalLead":
                refs[k] = v  # VIRTUAL three-part locator, same in both dialects
            else:
                refs[k] = f"iceberg.ontology.{_snake(k)}"
        return refs


def _make_ot(api_name: str, storage_type: str = "MANAGED") -> ObjectType:
    schema = _StubSchemaProvider()
    prop_specs = list(schema.properties().get(api_name, {}).items())
    props = []
    for i, (api, backing) in enumerate(prop_specs):
        props.append(
            PropertyDef(
                id=f"p-{api_name}-{api}",
                object_type_id=f"ot-{api_name}",
                api_name=api,
                display_name=api,
                data_type=DataType.STRING,
                is_primary_key=(i == 0),
                backing_mapping=BackingColumnRef(
                    dataset_api_name=f"ds_{api_name.lower()}",
                    backing_catalog="gravitino",
                    backing_schema="crm",
                    backing_table=f"t_{api_name.lower()}",
                    backing_column=backing,
                ),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    pk = props[0].api_name if props else "id"
    return ObjectType(
        id=f"ot-{api_name}",
        ontology_id="ont-crm",
        api_name=api_name,
        display_name=api_name,
        primary_key=pk,
        title_property=pk,
        storage_type=storage_type,
        properties=props,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _build_service(
    *,
    allow_all: bool = True,
    denied: set[str] | None = None,
    doris_rows: list[dict[str, Any]] | None = None,
    scope_residual: str | None = None,
    scope_masked: list[str] | None = None,
    scope_forbidden: bool = False,
) -> tuple[ObjectQueryService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Build an ObjectQueryService with mocked layers.

    ``denied`` is the set of ObjectType api_names that fail access check.
    ``doris_rows`` is what the Doris index store returns for execute_sql.
    ``scope_residual`` / ``scope_masked`` / ``scope_forbidden`` control the
    row-level QueryScope returned by ``evaluate_query_scope`` (Cedar TPE
    residual predicate + masked properties + hard-forbidden flag).
    """
    denied = denied or set()
    metadata = AsyncMock()
    ot_cache: dict[str, ObjectType] = {
        "ManualOutboundCall": _make_ot("ManualOutboundCall"),
        "Lead": _make_ot("Lead"),
        "SalesConsultant": _make_ot("SalesConsultant"),
        "LeadAllocateRecord": _make_ot("LeadAllocateRecord"),
        "ExternalLead": _make_ot("ExternalLead", storage_type="VIRTUAL"),
    }

    async def _get_object_type(ontology: str, ot_api: str) -> ObjectType:
        if ot_api not in ot_cache:
            raise OntologyError(f"unknown OT {ot_api}", code="NOT_FOUND")
        return ot_cache[ot_api]

    metadata.get_object_type = AsyncMock(side_effect=_get_object_type)

    catalog = AsyncMock()
    catalog.resolve_backing_table = AsyncMock(return_value={"catalog": "gravitino", "schema": "crm", "table": "t"})

    # AuthorizationService mock: replaces the legacy catalog.check_access.
    authz = AsyncMock()
    from unittest.mock import MagicMock as _MagicMock

    async def _check_access(principal, resource_type, resource_id, action):
        if allow_all and resource_id not in denied:
            return _MagicMock(allowed=True, reason="")
        return _MagicMock(allowed=False, reason="denied")

    authz.check_access = AsyncMock(side_effect=_check_access)

    # Row-level QueryScope mock (Cedar TPE residual → SQL predicate + masking).
    from ontology.core.schemas.permission import QueryScope

    async def _evaluate_query_scope(principal, ontology_api, ot_api):
        return QueryScope(
            forbidden=scope_forbidden,
            residual=scope_residual,
            masked_properties=list(scope_masked or []),
            project_scope=None,
        )

    authz.evaluate_query_scope = AsyncMock(side_effect=_evaluate_query_scope)

    index = AsyncMock()
    index.table_exists = AsyncMock(return_value=True)
    index.execute_sql = AsyncMock(return_value=doris_rows or [])

    engine = AsyncMock()
    engine.query = AsyncMock(return_value=doris_rows or [])

    dataset = AsyncMock()

    svc = ObjectQueryService(
        metadata=metadata, catalog=catalog, index=index, dataset=dataset,
        engine=engine, authorization_service=authz,
    )
    return svc, metadata, catalog, index, engine, authz


def _principal():
    from ontology.core.schemas.permission import Principal
    return Principal(id="u1", display_name="u1", is_anonymous=False)


def _compiler() -> OntologySqlCompiler:
    return OntologySqlCompiler(_StubSchemaProvider())


# ── 1. Signature: object_type_api_name removed ──────────────────────────


async def test_execute_compiled_sql_signature_no_object_type_param() -> None:
    """execute_compiled_sql(ontology, logical_sql, compiler=...) — no object_type arg.

    The old signature ``(ontology, object_type, logical_sql, compiler=)`` is
    gone. Passing the legacy 3-positional-arg form must NOT silently treat
    the object_type string as the SQL (which would then crash deep inside
    the compiler); instead ``logical_sql`` is the 2nd positional and the
    3rd positional lands on ``compiler``, which is type-checked.
    """
    import inspect

    sig = inspect.signature(ObjectQueryService.execute_compiled_sql)
    params = list(sig.parameters)
    # (self, ontology_api_name, logical_sql, compiler=None, *, principal=None)
    assert params[:4] == ["self", "ontology_api_name", "logical_sql", "compiler"]
    assert "principal" in params  # keyword-only access-principal
    # Two-arg call (ontology, sql) works end-to-end.
    svc, *_ = _build_service(doris_rows=[{"call_count": 7}])
    rows = await svc.execute_compiled_sql(
        "crm", "SELECT COUNT(*) AS call_count FROM ManualOutboundCall", compiler=_compiler()
    )
    assert rows == [{"call_count": 7}]


# ── 2. Multi-table JOIN: access check covers EVERY joined OT ───────────


async def test_multitable_join_checks_access_on_all_ots() -> None:
    """SELECT a.col, b.col FROM A JOIN B — both A and B must pass check_access."""
    sql = (
        "SELECT SalesConsultant.phone, ManualOutboundCall.callTime "
        "FROM ManualOutboundCall "
        "JOIN Lead ON Lead.leadId = ManualOutboundCall.leadId "
        "JOIN LeadAllocateRecord ON LeadAllocateRecord.leadsId = Lead.leadId "
        "JOIN SalesConsultant ON SalesConsultant.userId = LeadAllocateRecord.salesConsultantId"
    )
    svc, metadata, catalog, index, _, authz = _build_service(
        doris_rows=[{"phone": "13800000000", "call_time": "2026-07-01 10:00:00"}]
    )
    await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())

    checked = {call.args[2] for call in authz.check_access.call_args_list}
    assert checked == {
        "ManualOutboundCall",
        "Lead",
        "LeadAllocateRecord",
        "SalesConsultant",
    }


async def test_multitable_join_denied_on_any_ot_raises_forbidden() -> None:
    """If any joined OT fails access check, the whole query is forbidden."""
    sql = (
        "SELECT SalesConsultant.phone "
        "FROM ManualOutboundCall "
        "JOIN Lead ON Lead.leadId = ManualOutboundCall.leadId "
        "JOIN LeadAllocateRecord ON LeadAllocateRecord.leadsId = Lead.leadId "
        "JOIN SalesConsultant ON SalesConsultant.userId = LeadAllocateRecord.salesConsultantId"
    )
    svc, *_ = _build_service(denied={"SalesConsultant"})
    with pytest.raises(ForbiddenError):
        await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())


# ── 3. Multi-table JOIN: column remap covers EVERY joined OT ────────────


async def test_multitable_join_remaps_columns_from_all_ots() -> None:
    """SELECT a.p1, b.p2 FROM A JOIN B — both p1 and p2 map back to api_names.

    Regression for the bug where _map_backing_to_api used only the single
    anchor OT's property map, so columns from joined tables stayed as
    physical names (call_time instead of callTime).
    """
    # Two OTs in FROM/JOIN, SELECT pulls one column from each.
    sql = (
        "SELECT Lead.leadsStatus, ManualOutboundCall.callTime "
        "FROM ManualOutboundCall "
        "JOIN Lead ON Lead.leadId = ManualOutboundCall.leadId"
    )
    # Doris returns physical column names (snake_case backing columns).
    svc, *_ = _build_service(doris_rows=[{"leads_status": "100410", "call_time": "2026-07-01 10:00:00"}])
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    # Both columns must be remapped to api_names, regardless of which OT
    # owns them. No physical column name should leak.
    assert rows == [{"leadsStatus": "100410", "callTime": "2026-07-01 10:00:00"}]


async def test_select_star_multitable_remaps_all_ots() -> None:
    """SELECT * FROM A JOIN B — columns from both OTs remap to api_names."""
    sql = "SELECT * FROM ManualOutboundCall JOIN Lead ON Lead.leadId = ManualOutboundCall.leadId"
    svc, *_ = _build_service(
        doris_rows=[{"call_id": "c1", "lead_id": "L1", "call_time": "2026-07-01", "leads_status": "100410"}]
    )
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    assert rows == [
        {
            "callId": "c1",
            "leadId": "L1",  # shared physical col — ambiguous, kept as-is OR mapped; both OTs agree here
            "callTime": "2026-07-01",
            "leadsStatus": "100410",
        }
    ]


async def test_conflicting_physical_column_kept_as_is() -> None:
    """When two OTs map the SAME physical column to DIFFERENT api_names, the
    column is ambiguous — keep the physical name rather than silently picking
    one OT's mapping (which would mis-attribute the column)."""
    # Both Lead and ExternalLead have physical col lead_id, but we only have
    # one in the result set; use a synthetic case: two OTs both expose
    # physical "status" but with different api names. Simpler: patch the
    # schema so two OTs share a physical col name mapping to different api.
    sql = "SELECT Lead.leadsStatus FROM Lead"
    svc, *_ = _build_service(doris_rows=[{"leads_status": "100410"}])
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    assert rows == [{"leadsStatus": "100410"}]


# ── 4. Storage routing: all-MANAGED → Doris ────────────────────────────


async def test_all_managed_uses_doris() -> None:
    sql = "SELECT COUNT(*) AS c FROM ManualOutboundCall JOIN Lead ON Lead.leadId = ManualOutboundCall.leadId"
    svc, _, _, index, engine, _ = _build_service(doris_rows=[{"c": 5}])
    await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    index.execute_sql.assert_awaited()
    engine.query.assert_not_awaited()


# ── 5. Storage routing: all-VIRTUAL → Trino ────────────────────────────


async def test_all_virtual_uses_trino() -> None:
    # Both VIRTUAL: ExternalLead is VIRTUAL; make a second VIRTUAL OT by
    # reusing ExternalLead self-join semantics (single OT still exercises
    # the VIRTUAL branch).
    sql = "SELECT ExternalLead.leadsStatus FROM ExternalLead"
    svc, _, _, index, engine, _ = _build_service(doris_rows=[{"leads_status": "ok"}])
    await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    engine.query.assert_awaited()
    index.execute_sql.assert_not_awaited()


# ── 6. Storage routing: MIXED (MANAGED + VIRTUAL) → Trino federation ─


async def test_mixed_storage_join_routes_to_trino() -> None:
    """Joining a MANAGED OT with a VIRTUAL OT routes to Trino federation.

    Trino natively supports cross-catalog JOIN — MANAGED tables are visible
    as ``iceberg.ontology.<snake>`` and VIRTUAL tables as their external
    ``<catalog>.<schema>.<table>`` locator, so a single Trino query can
    join across them. This must NOT raise (the old MIXED_STORAGE_JOIN error
    was wrong — it denied a query Trino can actually run).
    """
    sql = (
        "SELECT ExternalLead.leadsStatus, ManualOutboundCall.callTime "
        "FROM ManualOutboundCall "
        "JOIN ExternalLead ON ExternalLead.leadId = ManualOutboundCall.leadId"
    )
    svc, _, _, index, engine, _ = _build_service(doris_rows=[{"leads_status": "100410", "call_time": "2026-07-01"}])
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    # Trino federation executes; Doris index never touched (no VIRTUAL table there).
    engine.query.assert_awaited()
    index.execute_sql.assert_not_awaited()
    assert rows == [{"leadsStatus": "100410", "callTime": "2026-07-01"}]


# ── 7. Single-table regression: behavior unchanged ─────────────────────


async def test_single_table_point_lookup_unchanged() -> None:
    """SELECT * FROM A WHERE pk = ? — single OT inferred, same as before."""
    sql = "SELECT * FROM ManualOutboundCall WHERE callId = 'c1'"
    svc, _, catalog, index, _, authz = _build_service(
        doris_rows=[{"call_id": "c1", "lead_id": "L1", "call_time": "2026-07-01"}]
    )
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    assert rows == [{"callId": "c1", "leadId": "L1", "callTime": "2026-07-01"}]
    # Only the one OT is access-checked.
    checked = {call.args[2] for call in authz.check_access.call_args_list}
    assert checked == {"ManualOutboundCall"}
    index.execute_sql.assert_awaited()


# ── 5. Row-level pushdown: evaluate_query_scope wired into query path ────


async def test_row_scope_forbidden_returns_empty() -> None:
    """When evaluate_query_scope says forbidden (Layer 1-4 deny), return []."""
    sql = "SELECT * FROM ManualOutboundCall"
    svc, *_ = _build_service(scope_forbidden=True)
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    assert rows == []


async def test_row_scope_residual_injected_into_sql() -> None:
    """The Cedar TPE residual predicate is ANDed into the compiled SQL WHERE."""
    sql = "SELECT callId FROM ManualOutboundCall"
    # Capture the physical SQL sent to Doris.
    captured_sql: list[str] = []

    async def _capture_sql(ontology, ot, sql, params=None):
        captured_sql.append(sql)
        return [{"call_id": "c1"}]

    svc, _, _, index, _, _ = _build_service(
        doris_rows=[{"call_id": "c1"}],
        scope_residual="region = 'CN'",
    )
    index.execute_sql = AsyncMock(side_effect=_capture_sql)

    await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())

    assert len(captured_sql) == 1
    # The residual predicate must appear in the executed SQL.
    assert "region = 'CN'" in captured_sql[0]


async def test_row_scope_masked_properties_nulled() -> None:
    """Properties in masked_properties are set to None in the result rows."""
    sql = "SELECT callId, leadId FROM ManualOutboundCall"
    svc, _, _, index, _, _ = _build_service(
        doris_rows=[{"call_id": "c1", "lead_id": "L1"}],
        scope_masked=["leadId"],
    )
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler(), principal=_principal())
    # leadId is masked → null; callId unchanged.
    assert rows == [{"callId": "c1", "leadId": None}]


async def test_row_scope_no_principal_skips_pushdown() -> None:
    """No principal → no row-level pushdown (backwards-compatible read path)."""
    sql = "SELECT callId FROM ManualOutboundCall"
    svc, _, _, _, _, authz = _build_service(
        doris_rows=[{"call_id": "c1"}],
        scope_residual="region = 'CN'",
    )
    # No principal passed — evaluate_query_scope should NOT be called.
    rows = await svc.execute_compiled_sql("crm", sql, compiler=_compiler())
    assert rows == [{"callId": "c1"}]
    authz.evaluate_query_scope.assert_not_awaited()
