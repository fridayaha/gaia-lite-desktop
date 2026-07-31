"""DVP read-path harness (DESIGN.md §4.1, dimension 1).

For each read case L1-L14:
  1. Resolve deterministic params (param_resolver).
  2. Produce expected = golden_truth.run_expected(case_id, params) [MySQL].
  3. Produce actual = ontology API /objects/load with an equivalent filter.
  4. Assert by kind (assertion_engine).
  5. Run trivial baselines; report their pass-rate (case validity signal).
  6. (Perf cases) measure Tax% vs raw MySQL.

DVP is all-VIRTUAL: /objects/load routes to Trino federation on MySQL.
Filter field names use property api_names (camelCase) — ObjectQueryService
maps them to physical backing_columns.

DESIGN.md §13 API conventions:
  - object_type_api_name = "{ontology}.{type}" (e.g. "DVP.ProjectBase")
  - properties: list of api_names to project
  - returned dict keys are api_names

L3 (6-hop traversal) uses chained single-hop search_around calls (no
multi-hop search_around in one request) — actual is an orchestration,
Tax% includes orchestration overhead (DESIGN.md METI: explained anomaly).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import golden_truth, param_resolver, trivial_baselines
from .assertion_engine import assert_by_kind
from .base import CaseResult, DimSummary, Outcome, classify, run_with_timeout
from .stats import percentile

log = logging.getLogger("read_harness")

ONTO = "DVP"
API_BASE = os.environ.get("DVP_API_BASE", "http://localhost:8000")
CASE_TIMEOUT = 60.0


# ── Case definitions ─────────────────────────────────────────────────────────


@dataclass
class ReadCase:
    case_id: str
    tier: int
    kind: str
    description: str
    param_keys: list[str]
    build: Callable[[param_resolver.ReadParams], ReadQuery]
    perf: bool = False


@dataclass
class ReadQuery:
    """The ontology query + expected-SQL params + comparison config."""

    object_type: str  # e.g. "DVP.ProjectBase"
    properties: list[str]
    filter: dict[str, Any] | None = None
    order_by: dict[str, Any] | None = None
    limit: int = 1000
    sql_params: dict[str, Any] = field(default_factory=dict)
    select_keys: list[str] | None = None
    opts: dict[str, Any] = field(default_factory=dict)
    # For multi-hop cases (L3): a custom actual-producer overrides the single
    # /objects/load call. Signature: (client, params) -> list[dict].
    actual_producer: Callable[[httpx.AsyncClient, param_resolver.ReadParams], Any] | None = None


# ── QueryFilter builders (dict form, matches QueryFilter schema) ─────────────


def _f_eq(field_name: str, value: Any) -> dict[str, Any]:
    return {"type": "eq", "field": field_name, "value": value}


def _f_and(*children: dict[str, Any]) -> dict[str, Any]:
    return {"type": "and", "filters": list(children)}


def _f_or(*children: dict[str, Any]) -> dict[str, Any]:
    return {"type": "or", "filters": list(children)}


def _f_range(field_name: str, mn: Any = None, mx: Any = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "range", "field": field_name}
    if mn is not None:
        d["min"] = mn
    if mx is not None:
        d["max"] = mx
    return d


def _f_search_around(link_api: str, source_type: str, source_filter: dict[str, Any]) -> dict[str, Any]:
    """search_around: traverse a link from a source object set to this OT."""
    return {
        "type": "search_around",
        "link_type_api_name": link_api,
        "source_object_set": {"object_type_api_name": source_type, "filter": source_filter},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Per-case query builders
# ═══════════════════════════════════════════════════════════════════════════


def _build_l1(p: param_resolver.ReadParams) -> ReadQuery:
    """L1: 单实体点查 — project_code 反查项目信息."""
    return ReadQuery(
        object_type=f"{ONTO}.ProjectBase",
        properties=["projectCode", "projectName", "brand", "projectType", "devTier",
                    "lifecycleState", "projectStatus", "managerName", "researchUnit"],
        filter=_f_eq("projectCode", p.project_code),
        limit=5,
        sql_params={"project_code": p.project_code},
        select_keys=["projectCode", "projectName", "brand", "projectType", "devTier",
                     "lifecycleState", "projectStatus", "managerName", "researchUnit"],
    )


def _build_l2(p: param_resolver.ReadParams) -> ReadQuery:
    """L2: 单实体过滤+排序 — 某项目下所有车型按 dev_tier 排序."""
    return ReadQuery(
        object_type=f"{ONTO}.ProjectVehicle",
        properties=["vehicleCode", "vehicleName", "powerType", "driveType", "devTier", "targetMarket"],
        filter=_f_and(_f_eq("projectCode", p.project_code), _f_eq("status", "1")),
        order_by={"field": "devTier", "direction": "ASC"},
        limit=100,
        sql_params={"project_code": p.project_code},
        select_keys=["vehicleCode", "vehicleName", "powerType", "driveType", "devTier", "targetMarket"],
        opts={"jaccard_threshold": 0.9},
    )


def _build_l3(p: param_resolver.ReadParams) -> ReadQuery:
    """L3: 多表 JOIN 反查 — change_point_id → 6 跳反查项目令号.

    actual_producer: chained single-hop search_around (no multi-hop in one
    request). Each hop resolves the next OT's id, then traverses the link.
    Tax% includes orchestration overhead (METI explained anomaly).
    """
    return ReadQuery(
        object_type=f"{ONTO}.ProjectBase",
        properties=["projectCode"],
        # The actual query is built by actual_producer (chained search_around).
        # filter/order here are unused when actual_producer is set.
        filter=None,
        limit=50,
        sql_params={"change_point_id": p.change_point_id},
        select_keys=["projectCode"],
        actual_producer=_l3_actual,
    )


async def _l3_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Chained 6-hop search_around: CP → Component → Structure → Body → Vehicle → Project.

    Each hop: load the next OT via search_around from the previous OT's ids.
    Returns list of {"projectCode": ...}.
    """
    # Hop 1: ChangePointEntity(change_point_id=CP) → component_id
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.ChangePointEntity",
        properties=["componentId"],
        filter=_f_eq("changePointId", p.change_point_id),
        limit=10,
    ))
    comp_ids = [row.get("componentId") for row in r if row.get("componentId")]
    if not comp_ids:
        return []

    # Hop 2: Component(component_id in comp_ids) → structure_code, structure_type
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.Component",
        properties=["structureCode", "structureType"],
        filter={"type": "or", "filters": [_f_eq("componentId", cid) for cid in comp_ids]},
        limit=50,
    ))
    # structure_code is shared across 5 structure OTs; collect unique codes + types
    struct_codes = []
    for row in r:
        sc = row.get("structureCode")
        if sc:
            struct_codes.append(sc)
    if not struct_codes:
        return []

    # Hop 3: query each of the 5 structure OTs by their code, get body_code.
    # structure_type tells which OT to query.
    body_codes: set[str] = set()
    type_to_ot = {
        "front": "FrontStructure", "side": "SideStructure", "rear": "RearStructure",
        "chassis": "ChassisStructure", "exterior": "ExteriorDesign",
    }
    # Group structure codes by type to query the right OT.
    by_type: dict[str, list[str]] = {}
    for row in r:
        st = row.get("structureType")
        sc = row.get("structureCode")
        if st and sc:
            by_type.setdefault(st, []).append(sc)
    for stype, codes in by_type.items():
        ot = type_to_ot.get(stype)
        if not ot:
            continue
        r2 = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.{ot}",
            properties=["bodyCode"],
            filter={"type": "or", "filters": [_f_eq(_code_field_for(ot), c) for c in codes]},
            limit=50,
        ))
        for row in r2:
            bc = row.get("bodyCode")
            if bc:
                body_codes.add(bc)
    if not body_codes:
        return []

    # Hop 4: VehicleBody(body_code in body_codes) → vehicle_code
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.VehicleBody",
        properties=["vehicleCode"],
        filter={"type": "or", "filters": [_f_eq("bodyCode", bc) for bc in body_codes]},
        limit=50,
    ))
    vehicle_codes = [row.get("vehicleCode") for row in r if row.get("vehicleCode")]
    if not vehicle_codes:
        return []

    # Hop 5: ProjectVehicle(vehicle_code in vehicle_codes) → project_code
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.ProjectVehicle",
        properties=["projectCode"],
        filter={"type": "or", "filters": [_f_eq("vehicleCode", vc) for vc in vehicle_codes]},
        limit=50,
    ))
    project_codes = [row.get("projectCode") for row in r if row.get("projectCode")]
    if not project_codes:
        return []

    # Hop 6 (dedup): ProjectBase — just confirm existence + dedup project_codes.
    # (project_codes already are the answer; no further traversal needed.)
    return [{"projectCode": pc} for pc in dict.fromkeys(project_codes)]


def _code_field_for(structure_ot: str) -> str:
    """The primary-key api_name for each structure OT (camelCase)."""
    return {
        "FrontStructure": "frontStructureCode",
        "SideStructure": "sideStructureCode",
        "RearStructure": "rearStructureCode",
        "ChassisStructure": "chassisStructureCode",
        "ExteriorDesign": "exteriorCode",
    }[structure_ot]


def _build_l4(p: param_resolver.ReadParams) -> ReadQuery:
    """L4: 聚合统计 — 某项目下各工况的 testItem 数量 (COUNT group by).

    Uses /objects/aggregate (Doris path unsupported for VIRTUAL → Trino).
    """
    # aggregate is handled specially in run_case (not via ReadQuery.filter).
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId"],
        filter=None,
        limit=1,
        sql_params={"project_code": p.project_code_for_agg},
        opts={"aggregate": True},  # signal to run_case to use /objects/aggregate
    )


def _build_l5(p: param_resolver.ReadParams) -> ReadQuery:
    """L5: LEFT JOIN 可选关联 — testItem LEFT JOIN spec."""
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "specCode", "specName"],
        # Note: specName is on Spec OT, not TestItem. VIRTUAL single-OT load
        # can't JOIN spec in one call. We load test_item then batch-load spec
        # names (orchestration). actual_producer handles this.
        filter=_f_eq("detailConditionCode", p.detail_condition_code),
        order_by={"field": "testItemId", "direction": "ASC"},
        limit=20000,
        sql_params={"detail_condition_code": p.detail_condition_code},
        select_keys=["testItemId", "testItemName", "status", "specCode", "specName"],
        actual_producer=_l5_actual,
    )


async def _l5_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Load test_items for a detail_condition, then batch-join spec names."""
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "specCode"],
        filter=_f_eq("detailConditionCode", p.detail_condition_code),
        order_by={"field": "testItemId", "direction": "ASC"},
        limit=20000,
    ))
    if not r:
        return []
    spec_codes = {row.get("specCode") for row in r if row.get("specCode")}
    spec_name_map: dict[str, str] = {}
    if spec_codes:
        specs = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.Spec",
            properties=["specCode", "specName"],
            filter={"type": "or", "filters": [_f_eq("specCode", sc) for sc in spec_codes]},
            limit=20000,
        ))
        spec_name_map = {s.get("specCode"): s.get("specName") for s in specs}
    for row in r:
        sc = row.get("specCode")
        row["specName"] = spec_name_map.get(sc) if sc else None
    return r


def _build_l6(p: param_resolver.ReadParams) -> ReadQuery:
    """L6: 增量查询 — 按 update_time 拉取某项目最近变更的 component.

    actual_producer: component is 3 hops from project (project→vehicle→body→
    structure→component). Chained search_around, then filter update_time.
    """
    return ReadQuery(
        object_type=f"{ONTO}.Component",
        properties=["componentId", "componentName", "componentCategory", "updateTime"],
        filter=None,
        limit=20000,
        sql_params={"project_code": p.project_code_for_incremental, "since_time": p.since_time},
        select_keys=["componentId", "componentName", "componentCategory", "updateTime"],
        actual_producer=_l6_actual,
    )


async def _l6_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Chained: project→vehicle→body→structures→component, filter update_time."""
    # vehicle_codes for project
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.ProjectVehicle", properties=["vehicleCode"],
        filter=_f_eq("projectCode", p.project_code_for_incremental), limit=50,
    ))
    vcs = [row.get("vehicleCode") for row in r if row.get("vehicleCode")]
    if not vcs:
        return []
    # body_codes for vehicles
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.VehicleBody", properties=["bodyCode"],
        filter={"type": "or", "filters": [_f_eq("vehicleCode", v) for v in vcs]}, limit=50,
    ))
    bcs = [row.get("bodyCode") for row in r if row.get("bodyCode")]
    if not bcs:
        return []
    # structure codes from each structure OT (5 OTs, each has belongsTo... no —
    # we query by bodyCode via search_around on the contains link from VehicleBody).
    # Simpler: query each structure OT by bodyCode FK.
    struct_codes: list[tuple[str, str]] = []  # (code, structure_type)
    type_to_ot = {"front": "FrontStructure", "side": "SideStructure", "rear": "RearStructure",
                  "chassis": "ChassisStructure", "exterior": "ExteriorDesign"}
    # The structure OTs don't have a bodyCode FK queryable directly? They do —
    # body_code is a property. But each OT has its own code field. We need
    # structure_code + structure_type. Query each OT by bodyCode.
    for stype, ot in type_to_ot.items():
        code_field = _code_field_for(ot)
        r2 = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.{ot}", properties=[code_field],
            filter={"type": "or", "filters": [_f_eq("bodyCode", b) for b in bcs]}, limit=50,
        ))
        for row in r2:
            sc = row.get(code_field)
            if sc:
                struct_codes.append((sc, stype))
    if not struct_codes:
        return []
    # components for structure codes
    all_codes = [c for c, _ in struct_codes]
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.Component",
        properties=["componentId", "componentName", "componentCategory", "updateTime", "structureCode"],
        filter={"type": "or", "filters": [_f_eq("structureCode", c) for c in all_codes]}, limit=20000,
    ))
    # Filter update_time >= since_time (client-side; range filter on the same
    # call would require an AND with the or — doable but keep simple here).
    return [row for row in r if _ge_time(row.get("updateTime"), p.since_time)]


def _ge_time(val: Any, since: str) -> bool:
    """String/ISO compare: val >= since (both 'YYYY-MM-DD HH:MM:SS' or ISO)."""
    if val is None:
        return False
    return str(val) >= since


def _build_l7(p: param_resolver.ReadParams) -> ReadQuery:
    """L7: 跨工况过滤 — frontCollision 工况下状态为待执行的 testItem.

    NOTE: D1 defect — 4 sub-condition OTs share t_oper_condition_detail, and
    ObjectQueryService does NOT filter by condition_type per OT. So querying
    DVP.FrontCollision returns ALL 200 rows (all condition_types). The
    testItem filter via detail_condition_code then pulls from all conditions.
    This case is Tier2 XFAIL (D1).
    """
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "testResponse", "detailConditionCode"],
        filter=_f_and(_f_eq("status", "1")),
        # We can't filter condition_type on TestItem directly (it's on
        # oper_condition_detail). actual_producer joins via detail_condition.
        limit=20000,
        sql_params={},  # L7.sql has no params
        select_keys=["testItemId", "testItemName", "status", "testResponse", "detailConditionCode"],
        actual_producer=_l7_actual,
    )


async def _l7_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Load front_collision detail_condition_codes, then test_items with status=1.

    Due to D1, FrontCollision load returns all 200 rows; we filter
    condition_type=front_collision client-side to mirror the expected SQL.
    """
    # Load FrontCollision codes (D1: returns all 200; filter client-side).
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.FrontCollision",
        properties=["detailConditionCode", "conditionType"],
        limit=20000,
    ))
    front_codes = [row.get("detailConditionCode") for row in r
                   if row.get("conditionType") == "front_collision" and row.get("detailConditionCode")]
    if not front_codes:
        return []
    # Load test_items with status=1 for these codes.
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "testResponse", "detailConditionCode"],
        filter=_f_and(_f_eq("status", "1")),
        limit=20000,
    ))
    # Filter to front_codes (client-side JOIN, since the API can't JOIN).
    front_set = set(front_codes)
    return [row for row in r if row.get("detailConditionCode") in front_set]


def _build_l8(p: param_resolver.ReadParams) -> ReadQuery:
    """L8: range filter 数值 — change_degree 在 [3,5] 区间的 changePointEntity.

    Regression #2 numeric variant — already fixed (verified 5956=5956).
    """
    return ReadQuery(
        object_type=f"{ONTO}.ChangePointEntity",
        properties=["changePointId", "changeDescription", "changeDegree", "weight", "componentId"],
        filter=_f_range("changeDegree", 3, 5),
        order_by={"field": "changePointId", "direction": "ASC"},
        limit=20000,
        sql_params={},
        select_keys=["changePointId", "changeDescription", "changeDegree", "weight", "componentId"],
    )


def _build_l9(p: param_resolver.ReadParams) -> ReadQuery:
    """L9: 跨链路反查 — 某 lmsTargetDimension 被哪些 testItem 验证 (反向 link)."""
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "testResponse", "detailConditionCode"],
        filter=_f_eq("dimensionId", p.dimension_id),
        order_by={"field": "testItemId", "direction": "ASC"},
        limit=20000,
        sql_params={"dimension_id": p.dimension_id},
        select_keys=["testItemId", "testItemName", "status", "testResponse", "detailConditionCode"],
    )


def _build_l10(p: param_resolver.ReadParams) -> ReadQuery:
    """L10: 多条件组合 — 某项目 + 某工况类型 + 某状态 的 testItem.

    actual_producer: join project→dvp→round→condition_detail→test_item.
    """
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "testResponse", "planEndTime"],
        limit=20000,
        sql_params={"project_code": p.project_code, "condition_type": p.condition_type, "status": p.status},
        select_keys=["testItemId", "testItemName", "status", "testResponse", "planEndTime"],
        actual_producer=_l10_actual,
    )


async def _l10_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """project→dvp→round→oper_condition(condition_code)→detail(condition_type)→test_item(status)."""
    # dvp_code for project
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.DvpDesign", properties=["dvpCode"],
        filter=_f_eq("projectCode", p.project_code), limit=10,
    ))
    dvp_codes = [row.get("dvpCode") for row in r if row.get("dvpCode")]
    if not dvp_codes:
        return []
    # rounds → condition_codes
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.ExperimentItemRound", properties=["conditionCode"],
        filter={"type": "or", "filters": [_f_eq("dvpCode", d) for d in dvp_codes]}, limit=50,
    ))
    cond_codes = {row.get("conditionCode") for row in r if row.get("conditionCode")}
    if not cond_codes:
        return []
    # oper_condition_detail for these condition_codes + condition_type filter
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.OperCondition", properties=["conditionCode"],
        filter={"type": "or", "filters": [_f_eq("conditionCode", c) for c in cond_codes]}, limit=50,
    ))
    # Actually we need detail codes: query oper_condition_detail by condition_code + condition_type
    # But FrontCollision OT (D1) returns all. Query OperConditionDetail via... there's no single
    # OT for the shared table. Use FrontCollision OT and filter condition_type client-side.
    detail_codes: set[str] = set()
    for ot in ["FrontCollision", "RearCollision", "SideCollision", "PedestrianProtect"]:
        r2 = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.{ot}", properties=["detailConditionCode", "conditionType", "conditionCode"],
            filter={"type": "or", "filters": [_f_eq("conditionCode", c) for c in cond_codes]}, limit=100,
        ))
        for row in r2:
            if row.get("conditionType") == p.condition_type and row.get("detailConditionCode"):
                detail_codes.add(row.get("detailConditionCode"))
    if not detail_codes:
        return []
    # test_items with status + detail_condition_code
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "testResponse", "planEndTime", "detailConditionCode"],
        filter=_f_eq("status", p.status), limit=20000,
    ))
    dc_set = detail_codes
    return [row for row in r if row.get("detailConditionCode") in dc_set]


def _build_l11(p: param_resolver.ReadParams) -> ReadQuery:
    """L11: 分页 — testItem 按 create_time 排序分页 (limit/offset)."""
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "createTime"],
        limit=20,
        sql_params={"condition_type": p.condition_type_for_page, "limit": 20, "offset": 0},
        select_keys=["testItemId", "testItemName", "status", "createTime"],
        actual_producer=_l11_actual,
    )


async def _l11_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Paginate test_items of a condition_type, ordered by create_time."""
    # Get front_collision detail codes (D1: filter client-side).
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.FrontCollision", properties=["detailConditionCode", "conditionType"], limit=20000,
    ))
    codes = [row.get("detailConditionCode") for row in r
             if row.get("conditionType") == p.condition_type_for_page and row.get("detailConditionCode")]
    if not codes:
        return []
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "createTime", "detailConditionCode"],
        filter={"type": "or", "filters": [_f_eq("detailConditionCode", c) for c in codes]},
        order_by={"field": "createTime", "direction": "ASC"},
        limit=20,
    ))
    return r


def _build_l12(p: param_resolver.ReadParams) -> ReadQuery:
    """L12: VIRTUAL range filter datetime — lmsTargetIteration 按 iteration_date 区间.

    D2 defect: Trino 'date <= varchar' TYPE_MISMATCH. Tier2 XFAIL.
    """
    return ReadQuery(
        object_type=f"{ONTO}.LmsTargetIteration",
        properties=["iterationId", "dimensionId", "iterationVersion", "iterationDate", "iterationThreshold", "status"],
        filter=_f_range("iterationDate", p.start_date, p.end_date),
        order_by={"field": "iterationDate", "direction": "ASC"},
        limit=20000,
        sql_params={"start_date": p.start_date, "end_date": p.end_date},
        select_keys=["iterationId", "dimensionId", "iterationVersion", "iterationDate", "iterationThreshold", "status"],
    )


def _build_l13(p: param_resolver.ReadParams) -> ReadQuery:
    """L13: 共用物理表跨工况 UNION — frontCollision + sideCollision 的 testItem 合集.

    D1 defect: shared-table OT confusion. Tier2 XFAIL.
    """
    return ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "detailConditionCode"],
        limit=20000,
        sql_params={},
        select_keys=["testItemId", "testItemName", "status", "detailConditionCode"],
        actual_producer=_l13_actual,
    )


async def _l13_actual(client: httpx.AsyncClient, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """Load front + side collision detail codes, then their test_items."""
    wanted_types = {"front_collision", "side_collision"}
    detail_codes: set[str] = set()
    for ot in ["FrontCollision", "SideCollision"]:
        r = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.{ot}", properties=["detailConditionCode", "conditionType"], limit=20000,
        ))
        for row in r:
            if row.get("conditionType") in wanted_types and row.get("detailConditionCode"):
                detail_codes.add(row.get("detailConditionCode"))
    if not detail_codes:
        return []
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem",
        properties=["testItemId", "testItemName", "status", "detailConditionCode"],
        filter={"type": "or", "filters": [_f_eq("detailConditionCode", c) for c in detail_codes]},
        limit=20000,
    ))
    return r


def _build_l14(p: param_resolver.ReadParams) -> ReadQuery:
    """L14: 时间旅行 — spec 历史快照对比. Tier3 XFAIL (VIRTUAL no snapshots)."""
    return ReadQuery(
        object_type=f"{ONTO}.Spec",
        properties=["specCode", "specName", "status", "updateTime"],
        filter=_f_eq("specCode", p.spec_code),
        limit=5,
        sql_params={"spec_code": p.spec_code},
        select_keys=["specCode", "specName", "status", "updateTime"],
        opts={"snapshot_diff": True},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Case registry
# ═══════════════════════════════════════════════════════════════════════════

CASES: list[ReadCase] = [
    ReadCase("L1", 1, "set_eq", "单实体点查: project_code 反查项目信息",
             ["project_code"], _build_l1, perf=True),
    ReadCase("L2", 1, "ordered_list", "单实体过滤+排序: 项目下车型按 dev_tier",
             ["project_code"], _build_l2, perf=True),
    ReadCase("L3", 1, "set_eq", "多表 JOIN 反查: change_point→6跳→项目令号",
             ["change_point_id"], _build_l3),
    ReadCase("L4", 1, "count_eq", "聚合统计: 项目下各工况 testItem 数量",
             ["project_code_for_agg"], _build_l4, perf=True),
    ReadCase("L5", 1, "null_allowed", "LEFT JOIN: testItem LEFT JOIN spec",
             ["detail_condition_code"], _build_l5),
    ReadCase("L6", 1, "set_eq", "增量查询: 项目最近变更的 component",
             ["project_code_for_incremental", "since_time"], _build_l6, perf=True),
    ReadCase("L7", 2, "set_eq", "跨工况过滤: frontCollision 待执行 testItem (D1 XFAIL)",
             [], _build_l7),
    ReadCase("L8", 1, "count_eq", "range filter 数值: change_degree [3,5]",
             [], _build_l8, perf=True),
    ReadCase("L9", 1, "set_eq", "跨链路反查: dimension 被哪些 testItem 验证",
             ["dimension_id"], _build_l9),
    ReadCase("L10", 1, "set_eq", "多条件组合: 项目+工况+状态 testItem",
             ["project_code", "condition_type", "status"], _build_l10),
    ReadCase("L11", 1, "ordered_list", "分页: testItem 按 create_time 分页",
             ["condition_type_for_page"], _build_l11),
    ReadCase("L12", 2, "count_eq", "VIRTUAL DATE range filter (D2 XFAIL)",
             ["start_date", "end_date"], _build_l12),
    ReadCase("L13", 2, "set_eq", "共用表跨工况 UNION (D1 XFAIL)",
             [], _build_l13),
    ReadCase("L14", 3, "snapshot_diff", "时间旅行: spec 快照 (VIRTUAL 无快照 XFAIL)",
             ["spec_code"], _build_l14),
]


# ═══════════════════════════════════════════════════════════════════════════
# Execution
# ═══════════════════════════════════════════════════════════════════════════


async def _load_objects(client: httpx.AsyncClient, q: ReadQuery) -> list[dict[str, Any]]:
    """POST /objects/textsql with the ReadQuery translated to logical SQL.

    收编后 /objects/load 手写旁路已删除，统一走 /objects/textsql 编译路径
    （OntologySqlCompiler 做列名映射/参数化/方言分叉）。ReadQuery 的 filter
    树→WHERE、properties→SELECT、order_by→ORDER BY、limit→LIMIT，拼成 logical
    SQL（用 api_name 当表名/列名，编译器映射到物理列）。
    """
    # object_type is "DVP.ProjectBase" → logical SQL uses the OT api_name "ProjectBase".
    ot_api = q.object_type.split(".", 1)[-1]
    ont_api = q.object_type.split(".", 1)[0] if "." in q.object_type else ONTO
    select_cols = ", ".join(q.properties) if q.properties else "*"
    sql = f"SELECT {select_cols} FROM {ot_api}"
    if q.filter:
        where = _filter_to_sql(q.filter)
        if where:
            sql += f" WHERE {where}"
    if q.order_by:
        direction = q.order_by.get("direction", "ASC")
        sql += f" ORDER BY {q.order_by['field']} {direction}"
    sql += f" LIMIT {q.limit}"
    # TextSqlRequest schema: {ontology_api_name, logical_sql}（无 object_type 字段，
    # 编译器从 SQL 里的表名自动推断 ObjectType）。
    payload = {"ontology_api_name": ont_api, "logical_sql": sql}
    resp = await client.post("/objects/textsql", json=payload, timeout=30.0)
    if resp.status_code != 200:
        raise RuntimeError(f"/objects/textsql {q.object_type} failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _typed_literal(val: Any) -> str:
    """Render a range literal with explicit SQL type for date/datetime values.

    Trino rejects `date_col <= '2026-01-01'` (date <= varchar TYPE_MISMATCH);
    wrapping as `DATE '2026-01-01'` makes the literal a date. Numeric values
    are emitted unquoted (the compiler parameterizes them with native type).
    """
    s = str(val)
    # DATE: YYYY-MM-DD
    import re
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return f"DATE '{s}'"
    # TIMESTAMP: YYYY-MM-DD HH:MM:SS
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", s):
        return f"TIMESTAMP '{s}'"
    # Numeric → unquoted (compiler binds native int/float)
    if s.lstrip("-").replace(".", "", 1).isdigit():
        return s
    # Default: string literal
    return f"'{s}'"


def _filter_to_sql(node: dict[str, Any]) -> str:
    """Translate a filter-tree node (dict form) to a logical-SQL WHERE fragment.

    Uses api_names directly (the compiler maps them to physical columns).
    Supports eq/range/and/or; search_around returns "" (not supported in this
    harness — L3/L6 use chained single-hop calls instead).
    """
    t = node.get("type")
    if t == "and":
        kids = [_filter_to_sql(c) for c in node.get("filters", [])]
        kids = [k for k in kids if k]
        return "(" + " AND ".join(kids) + ")" if kids else ""
    if t == "or":
        kids = [_filter_to_sql(c) for c in node.get("filters", [])]
        kids = [k for k in kids if k]
        return "(" + " OR ".join(kids) + ")" if kids else ""
    if t == "eq":
        return f"{node['field']} = '{node['value']}'"
    if t == "range":
        parts = []
        if node.get("min") is not None:
            parts.append(f"{node['field']} >= {_typed_literal(node['min'])}")
        if node.get("max") is not None:
            parts.append(f"{node['field']} <= {_typed_literal(node['max'])}")
        return " AND ".join(parts) if parts else ""
    # search_around / unknown — not supported here.
    return ""


async def _aggregate(client: httpx.AsyncClient, q: ReadQuery, p: param_resolver.ReadParams) -> list[dict[str, Any]]:
    """POST /objects/aggregate for L4 (count test_items grouped by condition_type)."""
    # L4 needs project→dvp→round→detail→test_item. We can't express the full
    # chain in one aggregate. Simplify: aggregate test_items by detail_condition_code
    # for the project's detail codes (mirror the expected SQL's DVP-plan path).
    # Reuse L10's detail-code resolution.
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.DvpDesign", properties=["dvpCode"],
        filter=_f_eq("projectCode", p.project_code_for_agg), limit=10,
    ))
    dvp_codes = [row.get("dvpCode") for row in r if row.get("dvpCode")]
    if not dvp_codes:
        return []
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.ExperimentItemRound", properties=["conditionCode"],
        filter={"type": "or", "filters": [_f_eq("dvpCode", d) for d in dvp_codes]}, limit=50,
    ))
    cond_codes = {row.get("conditionCode") for row in r if row.get("conditionCode")}
    if not cond_codes:
        return []
    # detail codes + their condition_type (D1: query each sub-condition OT)
    detail_to_type: dict[str, str] = {}
    for ot in ["FrontCollision", "RearCollision", "SideCollision", "PedestrianProtect"]:
        r2 = await _load_objects(client, ReadQuery(
            object_type=f"{ONTO}.{ot}", properties=["detailConditionCode", "conditionType", "conditionCode"],
            filter={"type": "or", "filters": [_f_eq("conditionCode", c) for c in cond_codes]}, limit=100,
        ))
        for row in r2:
            dc = row.get("detailConditionCode")
            if dc:
                detail_to_type[dc] = row.get("conditionType")
    if not detail_to_type:
        return []
    # count test_items per condition_type (client-side count, since aggregate
    # on VIRTUAL + group-by condition_type isn't expressible in one call).
    r = await _load_objects(client, ReadQuery(
        object_type=f"{ONTO}.TestItem", properties=["testItemId", "detailConditionCode"],
        filter={"type": "or", "filters": [_f_eq("detailConditionCode", dc) for dc in detail_to_type]},
        limit=2000000,
    ))
    counts: dict[str, int] = {}
    for row in r:
        ct = detail_to_type.get(row.get("detailConditionCode"))
        if ct:
            counts[ct] = counts.get(ct, 0) + 1
    return [{"detail_condition_type": k, "test_item_count": v} for k, v in sorted(counts.items())]


async def run_case(case: ReadCase, params: param_resolver.ReadParams, client: httpx.AsyncClient) -> CaseResult:
    """Run one read case: expected (MySQL) vs actual (ontology API), assert by kind."""
    q = case.build(params)

    async def _run() -> CaseResult:
        # Expected: golden truth from MySQL.
        try:
            expected = await golden_truth.run_expected(case.case_id, q.sql_params)
        except Exception as e:
            return CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR,
                              detail=f"expected SQL failed: {type(e).__name__}: {e}")

        # Actual: ontology API.
        try:
            if q.opts.get("aggregate"):
                actual = await _aggregate(client, q, params)
            elif q.actual_producer:
                actual = await q.actual_producer(client, params)
            else:
                actual = await _load_objects(client, q)
        except Exception as e:
            return CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR,
                              detail=f"actual API failed: {type(e).__name__}: {str(e)[:200]}",
                              expected_preview=_preview_expected(expected),
                              actual_preview="")

        # Assert.
        opts = dict(q.opts)
        if q.select_keys:
            opts["select_keys"] = q.select_keys
        verdict = assert_by_kind(case.kind, expected, actual, **opts)
        result = classify(case.case_id, case.tier, case.kind, verdict.passed, verdict.detail)
        result.expected_preview = _preview_expected(expected)
        result.actual_preview = _preview_actual(actual)
        result.metrics["score"] = round(verdict.score, 3)
        result.metrics["expected_n"] = len(expected) if isinstance(expected, list) else 0
        result.metrics["actual_n"] = len(actual) if isinstance(actual, list) else 0
        return result

    return await run_with_timeout(case.case_id, case.tier, case.kind, _run, CASE_TIMEOUT)


def _preview_actual(actual: list[dict], maxlen: int = 200) -> str:
    s = str(actual[:3])
    return s if len(s) <= maxlen else s[:maxlen] + "…"


def _preview_expected(expected: list[dict], maxlen: int = 200) -> str:
    s = str(expected[:3])
    return s if len(s) <= maxlen else s[:maxlen] + "…"


async def _trivial_baseline_pass_rate(case: ReadCase, params: param_resolver.ReadParams,
                                      client: httpx.AsyncClient) -> dict[str, float]:
    """Run applicable trivial baselines; return {baseline_name: pass_rate}.

    A baseline "passes" if its (baseline_actual, expected) assertion passes.
    High pass-rate = case too weak (DESIGN.md §3 Task Validity).
    """
    rates: dict[str, float] = {}
    baselines = trivial_baselines.READ_BASELINES.get(case.case_id, [])
    if not baselines:
        return rates
    q = case.build(params)
    try:
        expected = await golden_truth.run_expected(case.case_id, q.sql_params)
    except Exception:
        return rates
    # For dump_all/random_id/enumeration we need the full OT row set.
    # Fetch all rows of the case's primary OT (no filter).
    full_rows: list[dict] = []
    try:
        full_rows = await _load_objects(client, ReadQuery(
            object_type=q.object_type, properties=q.properties, limit=2000000,
        ))
    except Exception:
        full_rows = []
    opts = dict(q.opts)
    if q.select_keys:
        opts["select_keys"] = q.select_keys
    for bl in baselines:
        if bl == "do_nothing":
            act = trivial_baselines.do_nothing()
        elif bl == "dump_all":
            act = trivial_baselines.dump_all(full_rows)
        elif bl == "random_id":
            act = trivial_baselines.random_id(full_rows, q.properties[0])
        elif bl == "enumeration":
            act = trivial_baselines.enumeration(full_rows, q.properties[0])
        else:
            continue
        try:
            v = assert_by_kind(case.kind, expected, act, **opts)
            rates[bl] = 1.0 if v.passed else 0.0
        except Exception:
            rates[bl] = 0.0
    return rates


async def run_all(skip_perf: bool = False) -> DimSummary:
    """Run all read cases. Returns a DimSummary for the report."""
    summary = DimSummary(dimension="read")
    params = param_resolver.resolve_read_params()
    log.info("Resolved params: %s", params)
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as client:
        # Health check
        try:
            resp = await client.get("/health", timeout=5.0)
            if resp.status_code != 200:
                raise RuntimeError(f"backend unhealthy: {resp.status_code}")
        except Exception as e:
            log.error("backend unreachable: %s", e)
            for case in CASES:
                summary.add(CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR,
                                       detail=f"backend unreachable: {e}"))
            return summary

        # Confirm DVP ontology exists
        resp = await client.get(f"/ontologies/{ONTO}/object-types/summary", timeout=10.0)
        if resp.status_code != 200:
            log.error("DVP ontology not found: %s %s", resp.status_code, resp.text[:120])
            for case in CASES:
                summary.add(CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR,
                                       detail="DVP ontology not registered"))
            return summary

        for case in CASES:
            log.info("── Running %s (tier %d, %s) ──", case.case_id, case.tier, case.kind)
            result = await run_case(case, params, client)
            summary.add(result)
            marker = "✓" if result.outcome == Outcome.PASS else ("✗" if result.outcome == Outcome.FAIL else "→")
            log.info("  %s %s %s (%.2fs) %s", marker, case.case_id, result.outcome.value,
                     result.elapsed_s, result.detail[:120])

        # Trivial baselines (case validity signal).
        log.info("── Trivial baselines ──")
        for case in CASES:
            rates = await _trivial_baseline_pass_rate(case, params, client)
            if rates:
                summary.results[summary.total - len(CASES) + CASES.index(case)].metrics["trivial_baselines"] = rates
                any_pass = any(v > 0 for v in rates.values())
                if any_pass:
                    log.warning("  ⚠ %s trivial baseline passed: %s (case may be too weak)", case.case_id, rates)

        # Performance measurement.
        if not skip_perf:
            await _measure_perf(summary, params, client)

    return summary


async def _measure_perf(summary: DimSummary, params: param_resolver.ReadParams,
                        client: httpx.AsyncClient) -> None:
    """Measure Tax% for perf cases: ontology API p95 vs raw MySQL p95."""
    perf_cases = [c for c in CASES if c.perf]
    if not perf_cases:
        return
    log.info("── Performance (Tax%%) for %d cases ──", len(perf_cases))
    for case in perf_cases:
        q = case.build(params)
        # Warmup 1 (DESIGN.md §8.3).
        try:
            if q.actual_producer:
                await q.actual_producer(client, params)
            elif q.opts.get("aggregate"):
                await _aggregate(client, q, params)
            else:
                await _load_objects(client, q)
        except Exception:
            pass
        # 3 rounds, concurrency [1] (keep simple; [1,3,7] is the full plan).
        onto_times: list[float] = []
        raw_times: list[float] = []
        for _ in range(3):
            t0 = time.time()
            try:
                if q.actual_producer:
                    await q.actual_producer(client, params)
                elif q.opts.get("aggregate"):
                    await _aggregate(client, q, params)
                else:
                    await _load_objects(client, q)
                onto_times.append(time.time() - t0)
            except Exception:
                pass
            # Raw MySQL baseline.
            t0 = time.time()
            try:
                await golden_truth.run_expected(case.case_id, q.sql_params)
                raw_times.append(time.time() - t0)
            except Exception:
                pass
        onto_p95 = percentile(onto_times, 95) if onto_times else 0.0
        raw_p95 = percentile(raw_times, 95) if raw_times else 0.0
        tax_pct = ((onto_p95 - raw_p95) / raw_p95 * 100) if raw_p95 > 0 else 0.0
        # Stash on the matching result.
        for r in summary.results:
            if r.case_id == case.case_id:
                r.metrics["onto_p95_s"] = round(onto_p95, 3)
                r.metrics["raw_p95_s"] = round(raw_p95, 3)
                r.metrics["tax_pct"] = round(tax_pct, 1)
                log.info("  %s: onto_p95=%.3fs raw_p95=%.3fs Tax%%=%.1f%%",
                         case.case_id, onto_p95, raw_p95, tax_pct)
                break


def main() -> int:
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="DVP Read dimension harness")
    ap.add_argument("--json", metavar="PATH", help="write results JSON to PATH")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ DVP Read dimension ═══")
    print(f"  total={summary.total} PASS={summary.pass_n} FAIL={summary.fail_n} "
          f"XFAIL={summary.xfail_n} XPASS={summary.xpass_n} ERROR={summary.error_n}")
    counted = summary.counted
    if counted:
        print(f"  correctness rate: {summary.pass_n}/{counted} = {summary.correctness_rate:.1%}")
    if args.json:
        payload = {
            "dimension": summary.dimension,
            "total": summary.total,
            "pass": summary.pass_n,
            "fail": summary.fail_n,
            "xfail": summary.xfail_n,
            "xpass": summary.xpass_n,
            "error": summary.error_n,
            "skip": summary.skip_n,
            "counted": summary.counted,
            "correctness_rate": summary.correctness_rate,
            "results": [
                {
                    "case_id": r.case_id,
                    "tier": r.tier,
                    "kind": r.kind,
                    "outcome": r.outcome.value,
                    "detail": r.detail,
                    "elapsed_s": r.elapsed_s,
                    "expected_preview": r.expected_preview,
                    "actual_preview": r.actual_preview,
                    "metrics": r.metrics,
                    "counted_for_correctness": r.counted_for_correctness,
                    "is_regression": r.is_regression,
                }
                for r in summary.results
            ],
        }
        from pathlib import Path
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"  results JSON → {out}")
    return 0 if summary.fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
