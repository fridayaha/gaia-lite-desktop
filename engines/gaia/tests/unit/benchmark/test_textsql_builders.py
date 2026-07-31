"""Unit tests for the TextQL logical-SQL builders in the read harness.

These builders produce the apiName-only logical SQL that POST /objects/textsql
compiles to physical Doris SQL internally. The tests assert:

- output shape: (object_type, logical_sql, params)
- only ObjectType api_names appear as tables, only property api_names as columns
- param order matches the ? placeholders in the golden SQL semantics
- the anchor object_type is ``{ONTO}.{ObjectType}``
"""

from __future__ import annotations

import re

import pytest

from tests.benchmark.marketing.harness import param_resolver
from tests.benchmark.marketing.harness import read_harness as rh

# ObjectType api_names defined in the Marketing ontology seed.
MARKETING_OBJECT_TYPES = {
    "ManualOutboundCall",
    "Lead",
    "LeadAllocateRecord",
    "SalesConsultant",
    "Dealership",
    "User",
}


@pytest.fixture(scope="module")
def params() -> param_resolver.ReadParams:
    return param_resolver.resolve_read_params()


def _identifiers_in_sql(sql: str) -> set[str]:
    """Crude token extraction of bare identifiers (for apiName-leak checks)."""
    return {tok for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}


@pytest.mark.parametrize("case_id", ["L2", "L4", "L7"])
def test_builder_returns_anchored_object_type(case_id, params) -> None:
    ot, _sql, _params = rh.TEXTSQL_BUILDERS[case_id](params)
    assert ot.startswith("Marketing."), f"anchor must be {{ontology}}.{{OT}}, got {ot}"


def test_l4_builder_uses_only_apinames(params) -> None:
    """L4 logical SQL must reference only ObjectType/property api_names.

    Physical table names (idx_marketing__*) and physical columns (lead_id,
    sales_consultant_id, …) must NOT appear — the compiler rewrites them.
    """
    ot, sql, params_list = rh.TEXTSQL_BUILDERS["L4"](params)
    assert ot == "Marketing.ManualOutboundCall"
    # Tables referenced are all defined ObjectType api_names.
    for tbl in re.findall(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", sql):
        name = tbl[0] or tbl[1]
        assert name in MARKETING_OBJECT_TYPES, f"unknown ObjectType api_name: {name}"
    # Physical snake_case columns must not leak (api_names are camelCase).
    for snake in ["lead_id", "sales_consultant_id", "call_time", "leads_id", "user_id", "leads_status"]:
        assert snake not in sql, f"physical column leaked into logical SQL: {snake}"
    # camelCase api_names that should be present.
    for api in ["leadId", "leadsId", "salesConsultantId", "userId", "callTime", "leadsStatus", "phone"]:
        assert api in sql, f"expected property api_name missing: {api}"
    # COUNT aggregate shape matches golden L4.
    assert "COUNT(*) AS count" in sql
    # Param order: sales_phone then date (matches golden :sales_phone, :date).
    assert params_list == [params.sales_phone, params.date_pattern]


def test_l2_builder_semantics(params) -> None:
    ot, sql, params_list = rh.TEXTSQL_BUILDERS["L2"](params)
    assert ot == "Marketing.Lead"
    assert "ORDER BY l.nextFollowTime ASC" in sql
    assert "l.testDrive = '0'" in sql
    assert "l.leadsStatus = '100410'" in sql
    # 4-table join: LeadAllocateRecord, SalesConsultant, Lead, User.
    for tbl in ["LeadAllocateRecord", "SalesConsultant", "Lead", "User"]:
        assert tbl in sql
    assert params_list == [params.sales_phone, params.date_pattern]


def test_l7_builder_semantics(params) -> None:
    ot, sql, params_list = rh.TEXTSQL_BUILDERS["L7"](params)
    assert ot == "Marketing.Lead"
    # 5-table join including Dealership.
    for tbl in ["LeadAllocateRecord", "SalesConsultant", "Dealership", "Lead", "User"]:
        assert tbl in sql
    assert "d.storeCode = ?" in sql
    assert params_list == [params.store_code]


def test_run_textsql_substitutes_placeholders_positionally(params) -> None:
    """_run_textsql substitutes ? left-to-right and quotes string params.

    Uses a stub httpx client to capture the posted logical_sql without
    hitting the network. Verifies the harness only ever sends apiName-level
    SQL to the endpoint.
    """
    import asyncio

    class _StubResp:
        status_code = 200

        def json(self):
            return [{"count": 3}]

    class _StubClient:
        def __init__(self):
            self.posted = None

        async def post(self, path, json=None, timeout=None):
            self.posted = (path, json)
            return _StubResp()

    stub = _StubClient()
    ot, sql, plist = rh.TEXTSQL_BUILDERS["L4"](params)
    rows = asyncio.run(rh._run_textsql(stub, ot, sql, plist))
    assert rows == [{"count": 3}]
    path, body = stub.posted
    assert path == "/objects/textsql"
    assert body["object_type_api_name"] == ot
    sent_sql = body["logical_sql"]
    # No ? placeholders remain after substitution.
    assert "?" not in sent_sql
    # sales_phone (string) is quoted; date (string) is quoted.
    assert f"'{params.sales_phone}'" in sent_sql
    assert f"'{params.date_pattern}'" in sent_sql
    # Still apiName-level — no physical names introduced by the harness.
    assert "idx_marketing__" not in sent_sql
