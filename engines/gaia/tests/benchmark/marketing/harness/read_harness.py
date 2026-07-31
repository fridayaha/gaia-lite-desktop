"""Read-path harness (DESIGN.md §4.1, dimension 1).

For each read case L1-L8 (+ L3-bis, L7-bis):
  1. Resolve deterministic params (param_resolver).
  2. Produce expected = golden_truth.run_expected(case_id, params) [MySQL].
  3. Produce actual = ontology API /objects/load with an equivalent filter.
  4. Assert by kind (assertion_engine).
  5. Run trivial baselines; report their pass-rate (case validity signal).
  6. (Perf cases) measure Tax% vs raw MySQL.

The ontology query for each case is expressed as a LoadObjectsRequest
(QueryFilter tree) that mirrors the expected SQL's WHERE/ORDER. The filter
field names use property api_names (camelCase) — ObjectQueryService maps
them to physical backing_columns.

DESIGN.md §12 API conventions:
  - object_type_api_name = "{ontology}.{type}" (e.g. "Marketing.Lead")
  - properties: list of api_names to project
  - returned dict keys are api_names
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import golden_truth, param_resolver, trivial_baselines
from .assertion_engine import assert_by_kind
from .base import CaseResult, DimSummary, Outcome, classify, run_with_timeout
from .stats import geometric_mean, percentile

log = logging.getLogger("read_harness")

ONTO = "Marketing"
API_BASE = "http://localhost:8000"
CASE_TIMEOUT = 60.0


# ── Case definitions ─────────────────────────────────────────────────────────


@dataclass
class ReadCase:
    case_id: str
    tier: int
    kind: str
    description: str
    # Which params this case needs (subset of ReadParams fields).
    param_keys: list[str]
    # Builder: (params) -> (ontology_query, expected_sql_params, select_keys, opts)
    build: Callable[[param_resolver.ReadParams], ReadQuery]
    perf: bool = False  # whether to measure Tax%


@dataclass
class ReadQuery:
    """The ontology query + the expected-SQL params + comparison config."""

    object_type: str  # e.g. "Marketing.Lead"
    properties: list[str]  # api_names to project
    filter: dict[str, Any] | None = None  # QueryFilter tree (dict form)
    order_by: dict[str, Any] | None = None
    limit: int = 1000
    # expected SQL params (bound to :placeholders in the .sql file)
    sql_params: dict[str, Any] = field(default_factory=dict)
    # assertion opts (select_keys for set_eq, jaccard threshold, etc.)
    select_keys: list[str] | None = None
    opts: dict[str, Any] = field(default_factory=dict)


def _f_eq(field_name: str, value: Any) -> dict[str, Any]:
    return {"type": "eq", "field": field_name, "value": value}


def _f_and(*children: dict[str, Any]) -> dict[str, Any]:
    return {"type": "and", "filters": list(children)}


def _f_range(field_name: str, mn=None, mx=None) -> dict[str, Any]:
    r: dict[str, Any] = {"type": "range", "field": field_name}
    if mn is not None:
        r["min"] = mn
    if mx is not None:
        r["max"] = mx
    return r


# NOTE on filter semantics:
# The expected SQL uses LIKE 'YYYY-MM-DD%' (prefix match on a datetime). The
# ontology QueryFilter has no `like` — so for date-prefix cases we use a range
# [date 00:00, date+1day 00:00) which is equivalent for a DATE prefix. The
# read harness builds these range filters from the date string.


def _date_range_filter(field_name: str, date_str: str) -> dict[str, Any]:
    """[date 00:00:00, date+1day 00:00:00) — equivalent to LIKE 'date%'."""
    from datetime import datetime, timedelta

    d = datetime.strptime(date_str, "%Y-%m-%d")
    nxt = d + timedelta(days=1)
    return _f_range(field_name, mn=d.strftime("%Y-%m-%d %H:%M:%S"), mx=nxt.strftime("%Y-%m-%d %H:%M:%S"))


# ── Per-case builders ────────────────────────────────────────────────────────


def _build_l1(p: param_resolver.ReadParams) -> ReadQuery:
    # L1: lead_id → customer info (user_name, mobile). ObjectSet on Lead, filter lead id.
    # expected SQL joins lead→user; ontology: load Lead by id, project user via link?
    # Simpler: query User via lead.user_id. But the API filter is on Lead. We
    # load the Lead (filter id), then the harness reads user from the lead's
    # belongsToUser link. To keep it 1 call, we instead query the Lead OT and
    # project the lead's own user-derived fields? Lead has no user_name/mobile.
    # → Do it as 2 steps in the case runner (handled specially in run_case).
    return ReadQuery(
        object_type=f"{ONTO}.Lead",
        properties=["id", "userId"],
        filter=_f_eq("id", p.lead_id),
        limit=1,
        sql_params={"lead_id": p.lead_id},
        select_keys=["customer_name", "customer_phone"],
        opts={"_special": "L1_join_user"},
    )


def _build_l2(p: param_resolver.ReadParams) -> ReadQuery:
    # L2: 待邀约线索 — sales_phone + next_follow_time on date + leads_status + test_drive=0
    # The ontology filter on LeadAllocateRecord doesn't directly carry sales_phone;
    # the expected SQL joins through lead_allocate_record. Ontology: this is a
    # search_around (Lead ← allocate → sales). For the harness we approximate
    # by querying Lead with a filter that the runner resolves via a 2-step.
    return ReadQuery(
        object_type=f"{ONTO}.Lead",
        properties=["id", "nextFollowTime", "leadsStatus", "testDrive"],
        filter=_f_and(
            _f_eq("leadsStatus", "100410"),
            _f_eq("testDrive", "0"),
            _date_range_filter("nextFollowTime", p.date_pattern),
        ),
        order_by={"field": "nextFollowTime", "direction": "ASC"},
        limit=500,
        sql_params={"sales_phone": p.sales_phone, "date": p.date_pattern},
        select_keys=["customer_name", "customer_phone", "next_follow_time"],
        opts={"_special": "L2_sales_scoped", "sales_phone": p.sales_phone},
    )


def _build_l3(p: param_resolver.ReadParams) -> ReadQuery:
    # L3: lead_id → sales_consultant phone (via allocate). 2-step in runner.
    return ReadQuery(
        object_type=f"{ONTO}.Lead",
        properties=["id"],
        filter=_f_eq("id", p.lead_id),
        limit=1,
        sql_params={"lead_id": p.lead_id},
        select_keys=["sales_consultant_phone"],
        opts={"_special": "L3_allocate_sales"},
    )


def _build_l3_bis(p: param_resolver.ReadParams) -> ReadQuery:
    # L3-bis: test_drive_id → sales phone (via td.sale_id). Query TestDrive, then sales.
    return ReadQuery(
        object_type=f"{ONTO}.TestDrive",
        properties=["id", "saleId"],
        filter=_f_eq("id", p.test_drive_id),
        limit=1,
        sql_params={"test_drive_id": p.test_drive_id},
        select_keys=["sales_consultant_phone"],
        opts={"_special": "L3bis_td_sales"},
    )


def _build_l4(p: param_resolver.ReadParams) -> ReadQuery:
    # L4: COUNT manual_outbound_calls today for a sales (via lead.allocate).
    # ManualOutboundCall has no sales_consultant_id column; the golden SQL joins
    # moc → lead → allocate → sales. The harness mirrors this 3-hop join.
    return ReadQuery(
        object_type=f"{ONTO}.ManualOutboundCall",
        properties=["id"],
        filter=None,
        limit=10000,
        sql_params={"sales_phone": p.sales_phone, "date": p.date_pattern},
        select_keys=["count"],
        opts={"_special": "L4_count_via_lead_allocate", "sales_phone": p.sales_phone},
    )


def _sales_id_from_phone_holder(p: param_resolver.ReadParams) -> str:
    """Resolve the sales_consultant user_id from sales_phone (re-derive from seed)."""
    data = param_resolver._generate()
    for s in data.sales_consultant:
        if s["phone"] == p.sales_phone:
            return s["user_id"]
    return data.sales_consultant[0]["user_id"]


def _build_l5(p: param_resolver.ReadParams) -> ReadQuery:
    # L5: completed test drives on date + recording (LEFT JOIN). Query TestDrive.
    return ReadQuery(
        object_type=f"{ONTO}.TestDrive",
        properties=[
            "id",
            "saleId",
            "name",
            "phone",
            "scheduleTime",
            "beginTime",
            "endTime",
            "orderStatus",
            "originalRecordUrl",
            "testDriveCarId",
        ],
        filter=_date_range_filter("endTime", p.date_pattern),
        limit=500,
        sql_params={"date_pattern": p.date_pattern},
        select_keys=[
            "test_drive_id",
            "sc_phone",
            "customer_name",
            "customer_phone",
            "schedule_time",
            "start_time",
            "end_time",
            "order_status",
            "rec_url",
            "vehicle_model",
            "vehicle_variant",
        ],
        opts={"_special": "L5_recording_leftjoin"},
    )


def _build_l6(p: param_resolver.ReadParams) -> ReadQuery:
    # L6: sales_consultants updated after formatted_time. range on updateTime.
    return ReadQuery(
        object_type=f"{ONTO}.SalesConsultant",
        properties=[
            "userId",
            "userName",
            "phone",
            "jobNumber",
            "isStoreAdmin",
            "gender",
            "email",
            "leaveStatus",
            "terminationTime",
            "status",
            "updateTime",
            "storeCode",
        ],
        filter=_f_range("updateTime", mn=p.formatted_time),
        limit=1000,
        sql_params={"formatted_time": p.formatted_time},
        select_keys=[
            "sales_consultant_id",
            "sales_consultant_name",
            "phone",
            "job_number",
            "is_store_admin",
            "gender",
            "email",
            "leave_status",
            "termination_time",
            "status",
            "update_time",
            "dealership_id",
            "dealership_name",
        ],
        opts={"_special": "L6_default"},
    )


def _build_l7(p: param_resolver.ReadParams) -> ReadQuery:
    # L7: valid leads for a store's sales. 2-step (find sales in store, then leads).
    return ReadQuery(
        object_type=f"{ONTO}.Lead",
        properties=["id", "leadsStatus"],
        filter=_f_eq("leadsStatus", "100410"),
        limit=2000,
        sql_params={"store_code": p.store_code},
        select_keys=["lead_id", "customer_name", "customer_phone"],
        opts={"_special": "L7_store_scoped", "store_code": p.store_code},
    )


def _build_l7_bis(p: param_resolver.ReadParams) -> ReadQuery:
    # L7-bis: VIRTUAL competitive_analysis range filter (Tier2 xfail).
    return ReadQuery(
        object_type=f"{ONTO}.CompetitiveAnalysis",
        properties=["competitiveAnalysisId"],
        filter=_f_and(
            _f_eq("tdId", p.td_id_for_competitive),
            _f_range("confidenceScore", mn=p.confidence_min),
        ),
        limit=1000,
        sql_params={"td_id": p.td_id_for_competitive, "confidence_min": p.confidence_min},
        select_keys=["count"],
        opts={"_special": "L7bis_count"},
    )


def _build_l8(p: param_resolver.ReadParams) -> ReadQuery:
    # L8: user.phone_brand / phone_device_model = null (all_null).
    return ReadQuery(
        object_type=f"{ONTO}.User",
        properties=["userId", "phoneBrand", "phoneDeviceModel"],
        filter=_f_eq("userId", p.user_id),
        limit=1,
        sql_params={"user_id": p.user_id},
        select_keys=["phone_brand", "phone_device_model"],
        opts={"_special": "L8_all_null"},
    )


CASES: list[ReadCase] = [
    ReadCase("L1", 1, "set_eq", "lead_id → customer info", ["lead_id"], _build_l1, perf=True),
    ReadCase(
        "L2",
        1,
        "ordered_list",
        "待邀约线索过滤+排序 (regression #1)",
        ["sales_phone", "date_pattern"],
        _build_l2,
        perf=True,
    ),
    ReadCase("L3", 1, "set_eq", "lead_id → sales phone (via allocate)", ["lead_id"], _build_l3),
    ReadCase("L3-bis", 1, "set_eq", "test_drive → sales (fix 1 regression)", ["test_drive_id"], _build_l3_bis),
    ReadCase("L4", 1, "count_eq", "今日呼出数 COUNT", ["sales_phone", "date_pattern"], _build_l4, perf=True),
    ReadCase("L5", 1, "set_eq", "已完成试驾+录音 LEFT JOIN", ["date_pattern"], _build_l5),
    ReadCase("L6", 1, "set_eq", "增量同步销售顾问", ["formatted_time"], _build_l6, perf=True),
    ReadCase("L7", 1, "set_eq", "门店有效线索", ["store_code"], _build_l7),
    ReadCase(
        "L7-bis",
        2,
        "count_eq",
        "VIRTUAL range filter (regression #2)",
        ["td_id_for_competitive", "confidence_min"],
        _build_l7_bis,
    ),
    ReadCase("L8", 1, "all_null", "无源字段 phone_brand=null (fix 4)", ["user_id"], _build_l8),
]


# ── Ontology API call ────────────────────────────────────────────────────────


async def _load_objects(client: httpx.AsyncClient, q: ReadQuery) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "object_set": {
            "object_type_api_name": q.object_type,
            "filter": q.filter,
        },
        "properties": q.properties,
        "limit": q.limit,
    }
    if q.order_by:
        payload["order_by"] = q.order_by
    r = await client.post("/objects/load", json=payload, timeout=CASE_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"/objects/load {q.object_type} → {r.status_code}: {r.text[:300]}")
    return r.json()


# ── Special multi-step runners (cases that need link traversal) ──────────────
# These mirror the expected SQL's JOINs by chaining 2 ontology API calls.


async def _run_special(
    client: httpx.AsyncClient, case: ReadCase, q: ReadQuery, p: param_resolver.ReadParams
) -> list[dict[str, Any]]:
    """Handle the 2-step JOIN cases (L1/L2/L3/L3-bis/L4/L5/L7)."""
    special = q.opts.get("_special")

    if special == "L1_join_user":
        # Load Lead → get user_id → load User → return user_name/mobile.
        leads = await _load_objects(client, q)
        if not leads:
            return []
        lead = leads[0]
        uid = lead.get("userId")
        if not uid:
            # userId wasn't projected in the first call; re-query with it.
            lead_full = await _load_objects(
                client,
                ReadQuery(
                    object_type=q.object_type, properties=["id", "userId"], filter=_f_eq("id", lead.get("id")), limit=1
                ),
            )
            uid = lead_full[0].get("userId") if lead_full else None
        if not uid:
            return []
        users = await _load_objects(
            client,
            ReadQuery(
                object_type=f"{ONTO}.User",
                properties=["userId", "userName", "mobile"],
                filter=_f_eq("userId", uid),
                limit=1,
            ),
        )
        return [{"customer_name": u.get("userName"), "customer_phone": u.get("mobile")} for u in users]

    if special == "L2_sales_scoped":
        # Find leads for this sales_phone's sales consultant, then filter Lead.
        sc_id = _sales_id_from_phone_holder(p)
        # Find lead_ids allocated to this sales consultant.
        allocs = await _load_objects(
            client,
            ReadQuery(
                object_type=f"{ONTO}.LeadAllocateRecord",
                properties=["leadsId", "salesConsultantId"],
                filter=_f_eq("salesConsultantId", sc_id),
                limit=5000,
            ),
        )
        lead_ids = list({a["leadsId"] for a in allocs if a.get("leadsId")})
        if not lead_ids:
            return []
        # Load those leads, then apply the date/status/test_drive filter in-harness.
        # Use object_ids filter (object_set.object_ids).
        leads = []
        for batch in _chunks(lead_ids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.Lead", "object_ids": batch},
                    "properties": ["id", "nextFollowTime", "leadsStatus", "testDrive", "userId"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                leads.extend(r.json())
        from datetime import datetime, timedelta

        d = datetime.strptime(p.date_pattern, "%Y-%m-%d")
        nxt = d + timedelta(days=1)
        out = []
        for lead_row in leads:
            nf = lead_row.get("nextFollowTime")
            nf_dt = _parse_dt(nf)
            if nf_dt is not None:
                nf_dt = nf_dt.replace(tzinfo=None)
            if (
                nf_dt
                and d <= nf_dt < nxt
                and lead_row.get("leadsStatus") == "100410"
                and str(lead_row.get("testDrive")) == "0"
            ):
                out.append(lead_row)
        out.sort(
            key=lambda x: (
                (_parse_dt(x.get("nextFollowTime")) or datetime.max).replace(tzinfo=None)
                if _parse_dt(x.get("nextFollowTime"))
                else datetime.max
            )
        )
        # Enrich with user name/phone.
        uids = list({lead_row.get("userId") for lead_row in out if lead_row.get("userId")})
        users_by_id = {}
        for batch in _chunks(uids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.User", "object_ids": batch},
                    "properties": ["userId", "userName", "mobile"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                for u in r.json():
                    users_by_id[u.get("userId")] = u
        result = []
        for lead_row in out:
            u = users_by_id.get(lead_row.get("userId"), {})
            result.append(
                {
                    "sc_phone": p.sales_phone,
                    "customer_name": u.get("userName"),
                    "customer_phone": u.get("mobile"),
                    "next_follow_time": _iso(lead_row.get("nextFollowTime")),
                }
            )
        return result

    if special == "L3_allocate_sales":
        # lead_id → allocate records → sales phone.
        allocs = await _load_objects(
            client,
            ReadQuery(
                object_type=f"{ONTO}.LeadAllocateRecord",
                properties=["leadsId", "salesConsultantId"],
                filter=_f_eq("leadsId", p.lead_id),
                limit=100,
            ),
        )
        sc_ids = list({a["salesConsultantId"] for a in allocs if a.get("salesConsultantId")})
        if not sc_ids:
            return []
        sales = []
        for batch in _chunks(sc_ids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.SalesConsultant", "object_ids": batch},
                    "properties": ["userId", "phone"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                sales.extend(r.json())
        return [{"sales_consultant_phone": s.get("phone")} for s in sales]

    if special == "L3bis_td_sales":
        # test_drive_id → td.sale_id → sales phone.
        tds = await _load_objects(client, q)
        if not tds:
            return []
        td = tds[0]
        sale_id = td.get("saleId")
        if not sale_id:
            return []
        sales = await _load_objects(
            client,
            ReadQuery(
                object_type=f"{ONTO}.SalesConsultant",
                properties=["userId", "phone"],
                filter=_f_eq("userId", sale_id),
                limit=1,
            ),
        )
        return [{"sales_consultant_phone": s.get("phone")} for s in sales]

    if special == "L4_count_via_lead_allocate":
        # COUNT manual calls for sales on date, via the 3-hop join
        # moc → lead → allocate → sales. Mirror golden SQL L4.
        sc_id = _sales_id_from_phone_holder(p)
        # 1. leads allocated to this sales consultant.
        allocs = await _load_objects(
            client,
            ReadQuery(
                object_type=f"{ONTO}.LeadAllocateRecord",
                properties=["leadsId", "salesConsultantId"],
                filter=_f_eq("salesConsultantId", sc_id),
                limit=10000,
            ),
        )
        lead_ids = list({a["leadsId"] for a in allocs if a.get("leadsId")})
        if not lead_ids:
            return [{"count": 0}]
        # 2. lead_status for those leads (golden SQL filters leads_status=100410).
        lead_status = {}
        for batch in _chunks(lead_ids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.Lead", "object_ids": batch},
                    "properties": ["id", "leadsStatus"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                for lead_row in r.json():
                    lead_status[lead_row.get("id")] = lead_row.get("leadsStatus")
        valid_lead_ids = [lid for lid, st in lead_status.items() if st == "100410"]
        if not valid_lead_ids:
            return [{"count": 0}]
        # 3. manual calls whose lead_id is in valid_lead_ids AND call_time on date.
        from datetime import datetime, timedelta

        d = datetime.strptime(p.date_pattern, "%Y-%m-%d")
        nxt = d + timedelta(days=1)
        cnt = 0
        for lid in valid_lead_ids:
            calls = await _load_objects(
                client,
                ReadQuery(
                    object_type=f"{ONTO}.ManualOutboundCall",
                    properties=["id", "callTime", "leadId"],
                    filter=_f_eq("leadId", lid),
                    limit=500,
                ),
            )
            for c in calls:
                ct = _parse_dt(c.get("callTime"))
                if ct is not None:
                    ct = ct.replace(tzinfo=None)
                if ct and d <= ct < nxt:
                    cnt += 1
        return [{"count": cnt}]

    if special == "L5_recording_leftjoin":
        # Completed test drives on date + recording (LEFT JOIN).
        tds = await _load_objects(client, q)
        # Enrich: sales phone (via td.saleId), recording url, car series/model.
        sale_ids = list({t.get("saleId") for t in tds if t.get("saleId")})
        rec_ids = list({t.get("originalRecordUrl") for t in tds if t.get("originalRecordUrl")})
        car_ids = list({t.get("testDriveCarId") for t in tds if t.get("testDriveCarId")})
        sale_phone = await _batch_load_phone(client, sale_ids)
        rec_url = await _batch_load_recording_url(client, rec_ids)
        car_info = await _batch_load_car(client, car_ids)
        out = []
        for t in tds:
            cid = t.get("testDriveCarId")
            car = car_info.get(cid, {})
            out.append(
                {
                    "test_drive_id": t.get("id"),
                    "sc_phone": sale_phone.get(t.get("saleId")),
                    "customer_name": t.get("name"),
                    "customer_phone": t.get("phone"),
                    "schedule_time": _iso(t.get("scheduleTime")),
                    "start_time": _iso(t.get("beginTime")),
                    "end_time": _iso(t.get("endTime")),
                    "order_status": t.get("orderStatus"),
                    "rec_url": rec_url.get(t.get("originalRecordUrl")),
                    "vehicle_model": car.get("carSeriesName"),
                    "vehicle_variant": car.get("carModelName"),
                }
            )
        return out

    if special == "L7_store_scoped":
        # store_code → sales in store → their leads → valid leads + user info.
        sc_ids = await _sales_ids_for_store(client, p.store_code)
        if not sc_ids:
            return []
        allocs = []
        for batch in _chunks(sc_ids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {
                        "object_type_api_name": f"{ONTO}.LeadAllocateRecord",
                        "object_ids": None,
                        "filter": {"type": "eq", "field": "salesConsultantId", "value": None},
                    },
                    "properties": ["leadsId", "salesConsultantId"],
                    "limit": 1,
                },
                timeout=CASE_TIMEOUT,
            )
            # The above filter is wrong (None value); redo properly below.
            break
        # Properly: load allocate records per sales consultant via object_ids on
        # the SalesConsultant side is not possible; instead query LeadAllocateRecord
        # filtered by each sc_id.
        lead_ids = set()
        for sc_id in sc_ids:
            recs = await _load_objects(
                client,
                ReadQuery(
                    object_type=f"{ONTO}.LeadAllocateRecord",
                    properties=["leadsId", "salesConsultantId"],
                    filter=_f_eq("salesConsultantId", sc_id),
                    limit=2000,
                ),
            )
            for r in recs:
                if r.get("leadsId"):
                    lead_ids.add(r["leadsId"])
        if not lead_ids:
            return []
        leads = []
        for batch in _chunks(list(lead_ids), 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.Lead", "object_ids": batch},
                    "properties": ["id", "leadsStatus", "userId"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                leads.extend(r.json())
        valid = [lr for lr in leads if lr.get("leadsStatus") == "100410"]
        uids = list({lr.get("userId") for lr in valid if lr.get("userId")})
        users_by_id = {}
        for batch in _chunks(uids, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.User", "object_ids": batch},
                    "properties": ["userId", "userName", "mobile"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                for u in r.json():
                    users_by_id[u.get("userId")] = u
        return [
            {
                "lead_id": lr.get("id"),
                "customer_name": users_by_id.get(lr.get("userId"), {}).get("userName"),
                "customer_phone": users_by_id.get(lr.get("userId"), {}).get("mobile"),
            }
            for lr in valid
        ]

    if special == "L7bis_count":
        # VIRTUAL competitive_analysis count (Tier2 xfail).
        try:
            rows = await _load_objects(client, q)
            return [{"count": len(rows)}]
        except Exception as e:
            raise RuntimeError(f"L7-bis VIRTUAL query failed (expected xfail): {e}")

    if special == "L8_all_null":
        rows = await _load_objects(client, q)
        return [{"phone_brand": r.get("phoneBrand"), "phone_device_model": r.get("phoneDeviceModel")} for r in rows]

    if special == "L6_default":
        # L6 increment-sync: sales consultants updated after formatted_time.
        # Reshape API api_names → SQL-alias keys and enrich with dealership
        # name (golden SQL joins dealership for dealership_id/dealership_name).
        rows = await _load_objects(client, q)
        store_codes = list({r.get("storeCode") for r in rows if r.get("storeCode")})
        dealerships = {}
        for batch in _chunks(store_codes, 500):
            r = await client.post(
                "/objects/load",
                json={
                    "object_set": {"object_type_api_name": f"{ONTO}.Dealership", "object_ids": batch},
                    "properties": ["storeCode", "orgName"],
                    "limit": len(batch),
                },
                timeout=CASE_TIMEOUT,
            )
            if r.status_code == 200:
                for d in r.json():
                    dealerships[d.get("storeCode")] = d
        out = []
        for r in rows:
            d = dealerships.get(r.get("storeCode"), {})
            isa = r.get("isStoreAdmin")
            # Normalize boolean-ish '1'/'0' to int to match MySQL TINYINT.
            if isinstance(isa, str) and isa in ("0", "1"):
                isa = int(isa)
            out.append(
                {
                    "sales_consultant_id": r.get("userId"),
                    "sales_consultant_name": r.get("userName"),
                    "phone": r.get("phone"),
                    "job_number": r.get("jobNumber"),
                    "is_store_admin": isa,
                    "gender": r.get("gender"),
                    "email": r.get("email"),
                    "leave_status": r.get("leaveStatus"),
                    "termination_time": _iso(r.get("terminationTime")),
                    "status": r.get("status"),
                    "update_time": _iso(r.get("updateTime")),
                    "dealership_id": r.get("storeCode"),
                    "dealership_name": d.get("orgName"),
                }
            )
        return out

    # Default: single load_objects call.
    return await _load_objects(client, q)


async def _batch_load_phone(client: httpx.AsyncClient, user_ids: list[str]) -> dict[str, str]:
    out = {}
    for batch in _chunks(user_ids, 500):
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {"object_type_api_name": f"{ONTO}.SalesConsultant", "object_ids": batch},
                "properties": ["userId", "phone"],
                "limit": len(batch),
            },
            timeout=CASE_TIMEOUT,
        )
        if r.status_code == 200:
            for s in r.json():
                out[s.get("userId")] = s.get("phone")
    return out


async def _batch_load_recording_url(client: httpx.AsyncClient, rec_ids: list[str]) -> dict[str, str]:
    if not rec_ids:
        return {}
    out = {}
    for batch in _chunks(rec_ids, 500):
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {"object_type_api_name": f"{ONTO}.Recording", "object_ids": batch},
                "properties": ["recordingId", "recordingUrl"],
                "limit": len(batch),
            },
            timeout=CASE_TIMEOUT,
        )
        if r.status_code == 200:
            for rec in r.json():
                out[rec.get("recordingId")] = rec.get("recordingUrl")
    return out


async def _batch_load_car(client: httpx.AsyncClient, car_ids: list[str]) -> dict[str, dict]:
    if not car_ids:
        return {}
    out = {}
    for batch in _chunks(car_ids, 500):
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {"object_type_api_name": f"{ONTO}.TestDriveCar", "object_ids": batch},
                "properties": ["id", "carSeriesName", "carModelName"],
                "limit": len(batch),
            },
            timeout=CASE_TIMEOUT,
        )
        if r.status_code == 200:
            for c in r.json():
                out[c.get("id")] = c
    return out


async def _sales_ids_for_store(client: httpx.AsyncClient, store_code: str) -> list[str]:
    r = await client.post(
        "/objects/load",
        json={
            "object_set": {"object_type_api_name": f"{ONTO}.SalesConsultant", "filter": _f_eq("storeCode", store_code)},
            "properties": ["userId"],
            "limit": 500,
        },
        timeout=CASE_TIMEOUT,
    )
    if r.status_code != 200:
        return []
    return [s["userId"] for s in r.json() if s.get("userId")]


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _parse_dt(v: Any):
    from datetime import datetime

    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v)
    # Try ISO 8601 first (handles timezone offsets like +00:00 and Z).
    try:
        # fromisoformat accepts '2026-06-18T21:17:37' and '2026-06-18 21:17:37+00:00'
        # (space separator) in 3.11+.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s[:26].rstrip("Z"), fmt)
        except ValueError:
            continue
    return None


def _iso(v: Any) -> str | None:
    d = _parse_dt(v)
    if d is None:
        return str(v) if v is not None else None
    # Strip timezone (compare wall-clock only — MySQL datetimes are naive and
    # the API may attach +00:00; the underlying value is the same instant).
    return d.replace(tzinfo=None).isoformat()


# ── Main runner ──────────────────────────────────────────────────────────────


async def run_case(case: ReadCase, params: param_resolver.ReadParams, client: httpx.AsyncClient) -> CaseResult:
    q = case.build(params)
    # expected from MySQL
    expected = await golden_truth.run_expected(case.case_id, q.sql_params)
    # actual from ontology API (special multi-step or single load)
    actual = await _run_special(client, case, q, params)
    # assert
    opts = dict(q.opts)
    if q.select_keys:
        opts["select_keys"] = q.select_keys
    verdict = assert_by_kind(case.kind, expected, actual, **{k: v for k, v in opts.items() if not k.startswith("_")})
    r = classify(case.case_id, case.tier, case.kind, verdict.passed, verdict.detail)
    r.expected_preview = _preview_expected(expected) if expected else "[]"
    r.actual_preview = _preview_actual(actual)
    return r


def _preview_actual(actual: list[dict], maxlen: int = 200) -> str:
    s = str(actual[:3])
    return s if len(s) <= maxlen else s[:maxlen] + "…"


def _preview_expected(expected: list[dict], maxlen: int = 200) -> str:
    s = str(expected[:3])
    return s if len(s) <= maxlen else s[:maxlen] + "…"


async def _trivial_baseline_pass_rate(
    case: ReadCase, params: param_resolver.ReadParams, client: httpx.AsyncClient
) -> dict[str, bool]:
    """Run each trivial baseline; return whether it (wrongly) passes."""
    q = case.build(params)
    expected = await golden_truth.run_expected(case.case_id, q.sql_params)
    results = {}
    for name in trivial_baselines.READ_BASELINES.get(case.case_id, []):
        if name == "do_nothing":
            actual = trivial_baselines.do_nothing()
        elif name == "dump_all":
            # Dump all rows of the primary OT (best-effort).
            try:
                actual = await _load_objects(
                    client, ReadQuery(object_type=q.object_type, properties=q.properties, limit=10000)
                )
            except Exception:
                actual = []
        elif name == "random_id":
            try:
                rows = await _load_objects(
                    client, ReadQuery(object_type=q.object_type, properties=q.properties, limit=10000)
                )
            except Exception:
                rows = []
            actual = trivial_baselines.random_id(rows, "id")
        elif name == "enumeration":
            try:
                rows = await _load_objects(
                    client, ReadQuery(object_type=q.object_type, properties=q.properties, limit=10000)
                )
            except Exception:
                rows = []
            actual = trivial_baselines.enumeration(rows, "id")
        else:
            actual = []
        opts = dict(q.opts)
        if q.select_keys:
            opts["select_keys"] = q.select_keys
        try:
            v = assert_by_kind(
                case.kind, expected, actual, **{k: val for k, val in opts.items() if not k.startswith("_")}
            )
            results[name] = v.passed
        except Exception:
            results[name] = False
    return results


async def run_all() -> DimSummary:
    summary = DimSummary(dimension="read")
    params = param_resolver.resolve_read_params()
    log.info(
        "Params: lead_id=%s sales_phone=%s td=%s store=%s user=%s",
        params.lead_id,
        params.sales_phone,
        params.test_drive_id,
        params.store_code,
        params.user_id,
    )
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CASE_TIMEOUT + 10) as client:
        for case in CASES:
            log.info("── Read case %s (tier %d, %s) ──", case.case_id, case.tier, case.kind)

            async def _run():
                return await run_case(case, params, client)

            r = await run_with_timeout(case.case_id, case.tier, case.kind, _run, CASE_TIMEOUT)
            summary.add(r)
            log.info("  → %s (%.1fs): %s", r.outcome.value, r.elapsed_s, r.detail[:160])

            # Trivial baselines (only for tier-1 cases that PASS — else skip to save time).
            if case.tier == 1 and r.outcome in (Outcome.PASS,):
                try:
                    tb = await _trivial_baseline_pass_rate(case, params, client)
                    r.metrics["trivial_baselines"] = tb
                    if any(tb.values()):
                        log.warning("  ⚠ trivial baseline passed for %s: %s", case.case_id, tb)
                except Exception as e:
                    log.info("  (trivial baselines skipped: %s)", str(e)[:80])

        # Performance sub-measurement (L1/L2/L4/L6).
        await _measure_perf(summary, params, client)
    return summary


# ── TextQL logical-SQL builders (ADR-012 Step 4 path B) ─────────────────────
# These express the same semantics as the golden SQL but with ObjectType
# api_names as table names and property api_names as columns. The
# OntologySqlCompiler (invoked inside /objects/textsql) rewrites them to
# physical Doris SQL — the harness never sees physical table/column names.
# Literal values are passed as ? placeholders and bound positionally.
#
# Only the multi-hop / aggregate cases where the single-call /objects/load
# API forces multi-call JOIN emulation get a TextSQL variant: L2 (filter+
# sort across 4 tables), L4 (COUNT across 4 tables), L7 (filter across 5
# tables). L1/L6 are single-table and already clean.


def _textsql_l4(p: param_resolver.ReadParams) -> tuple[str, str, list[Any]]:
    """L4 via TextQL: COUNT manual outbound calls today for a sales consultant.

    Mirrors golden L4.sql (moc → lead → allocate → sales) but in logical form.
    Returns (object_type, logical_sql, params).
    """
    sql = (
        "SELECT COUNT(*) AS count "
        "FROM ManualOutboundCall moc "
        "JOIN Lead l ON l.id = moc.leadId "
        "JOIN LeadAllocateRecord lar ON lar.leadsId = l.id "
        "JOIN SalesConsultant sc ON sc.userId = lar.salesConsultantId "
        "WHERE sc.phone = ? "
        "  AND moc.callTime LIKE CONCAT(?, '%') "
        "  AND l.leadsStatus = '100410'"
    )
    return f"{ONTO}.ManualOutboundCall", sql, [p.sales_phone, p.date_pattern]


def _textsql_l2(p: param_resolver.ReadParams) -> tuple[str, str, list[Any]]:
    """L2 via TextQL: pending-invite leads for a sales consultant on a date."""
    sql = (
        "SELECT sc.phone AS sc_phone, u.userName AS customer_name, "
        "       u.mobile AS customer_phone, l.nextFollowTime AS next_follow_time "
        "FROM LeadAllocateRecord lar "
        "JOIN SalesConsultant sc ON sc.userId = lar.salesConsultantId "
        "JOIN Lead l ON l.id = lar.leadsId "
        "JOIN User u ON u.userId = l.userId "
        "WHERE sc.phone = ? "
        "  AND l.nextFollowTime LIKE CONCAT(?, '%') "
        "  AND l.leadsStatus = '100410' "
        "  AND l.testDrive = '0' "
        "ORDER BY l.nextFollowTime ASC"
    )
    return f"{ONTO}.Lead", sql, [p.sales_phone, p.date_pattern]


def _textsql_l7(p: param_resolver.ReadParams) -> tuple[str, str, list[Any]]:
    """L7 via TextQL: valid leads for all sales in a store."""
    sql = (
        "SELECT l.id AS lead_id, u.userName AS customer_name, u.mobile AS customer_phone "
        "FROM LeadAllocateRecord lar "
        "JOIN SalesConsultant sc ON sc.userId = lar.salesConsultantId "
        "JOIN Dealership d ON d.storeCode = sc.storeCode "
        "JOIN Lead l ON l.id = lar.leadsId "
        "JOIN User u ON u.userId = l.userId "
        "WHERE d.storeCode = ? "
        "  AND l.leadsStatus = '100410'"
    )
    return f"{ONTO}.Lead", sql, [p.store_code]


TEXTSQL_BUILDERS: dict[str, Callable[[param_resolver.ReadParams], tuple[str, str, list[Any]]]] = {
    "L2": _textsql_l2,
    "L4": _textsql_l4,
    "L7": _textsql_l7,
}


async def _run_textsql(
    client: httpx.AsyncClient, object_type: str, logical_sql: str, sql_params: list[Any]
) -> list[dict[str, Any]]:
    """Call POST /objects/textsql with logical SQL; bind params as literals.

    The endpoint compiles logical→physical internally (harness sees only
    api_names). sql_params are substituted into ? placeholders here because
    the HTTP schema carries a SQL string, not a parameterized statement —
    the compiler still re-extracts literals into bound params internally
    (injection-safe by construction).
    """
    # Substitute ? placeholders with escaped literal values. The compiler
    # will re-extract these into bound ? params during compile(), so this is
    # a transport concern only — values are never interpolated into physical
    # SQL by the harness.
    sql = logical_sql
    for val in sql_params:
        if isinstance(val, str):
            escaped = "'" + val.replace("'", "''") + "'"
        else:
            escaped = str(val)
        sql = sql.replace("?", escaped, 1)
    r = await client.post(
        "/objects/textsql",
        json={"object_type_api_name": object_type, "logical_sql": sql},
        timeout=CASE_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"/objects/textsql {object_type} → {r.status_code}: {r.text[:300]}")
    return r.json()


async def _measure_perf(summary: DimSummary, params: param_resolver.ReadParams, client: httpx.AsyncClient) -> None:
    """Tax% = (onto_p95 - raw_p95)/raw_p95 for perf cases (DESIGN.md §4.1).

    For cases with a TextSQL builder, also measures textsql_p95 — the
    single-call path through /objects/textsql (OntologySqlCompiler + Doris),
    which avoids the multi-call JOIN emulation that inflates onto_p95 for
    L2/L4/L7. Both numbers are reported so the orchestration overhead is
    visible alongside the clean compile+execute signal.
    """
    perf_cases = [c for c in CASES if c.perf]
    if not perf_cases:
        return
    log.info("── Performance measurement (Tax%%) for %d cases ──", len(perf_cases))
    speedups = []
    textsql_speedups: list[float] = []
    for case in perf_cases:
        try:
            q = case.build(params)
            # warmup (1x, not timed)
            await _timed_call(client, case, q, params)
            onto_times = [t for _, t in [await _timed_call(client, case, q, params) for _ in range(3)]]
            # raw MySQL baseline
            raw_times = []
            for _ in range(3):
                t0 = time.perf_counter()
                await golden_truth.run_expected(case.case_id, q.sql_params)
                raw_times.append(time.perf_counter() - t0)
            onto_p95 = percentile(onto_times, 95)
            raw_p95 = percentile(raw_times, 95)
            tax = (onto_p95 - raw_p95) / raw_p95 if raw_p95 > 0 else 0.0
            speedup = raw_p95 / onto_p95 if onto_p95 > 0 else 0.0
            if speedup > 0:
                speedups.append(speedup)

            # TextSQL path (single-call compile+execute) for multi-hop cases.
            textsql_p95: float | None = None
            textsql_tax: float | None = None
            textsql_times: list[float] = []
            builder = TEXTSQL_BUILDERS.get(case.case_id)
            if builder is not None:
                ot, logical_sql, sql_params = builder(params)
                # warmup
                await _run_textsql(client, ot, logical_sql, sql_params)
                for _ in range(3):
                    t0 = time.perf_counter()
                    await _run_textsql(client, ot, logical_sql, sql_params)
                    textsql_times.append(time.perf_counter() - t0)
                textsql_p95 = percentile(textsql_times, 95)
                textsql_tax = (textsql_p95 - raw_p95) / raw_p95 if raw_p95 > 0 else 0.0
                ts_speedup = raw_p95 / textsql_p95 if textsql_p95 > 0 else 0.0
                if ts_speedup > 0:
                    textsql_speedups.append(ts_speedup)

            for r in summary.results:
                if r.case_id == case.case_id:
                    r.metrics["onto_p95_s"] = round(onto_p95, 4)
                    r.metrics["raw_p95_s"] = round(raw_p95, 4)
                    r.metrics["tax_pct"] = round(tax * 100, 1)
                    r.metrics["onto_times"] = [round(t, 4) for t in onto_times]
                    if textsql_p95 is not None:
                        r.metrics["textsql_p95_s"] = round(textsql_p95, 4)
                        r.metrics["textsql_tax_pct"] = round(textsql_tax * 100, 1)  # type: ignore[arg-type]
                        r.metrics["textsql_times"] = [round(t, 4) for t in textsql_times]
                    break
            if textsql_p95 is not None:
                log.info(
                    "  %s: onto_p95=%.4fs textsql_p95=%.4fs raw_p95=%.4fs tax=%.1f%% textsql_tax=%.1f%%",
                    case.case_id, onto_p95, textsql_p95, raw_p95, tax * 100, textsql_tax * 100,  # type: ignore[operator]
                )
            else:
                log.info("  %s: onto_p95=%.4fs raw_p95=%.4fs tax=%.1f%%", case.case_id, onto_p95, raw_p95, tax * 100)
        except Exception as e:
            log.warning("  %s perf skipped: %s", case.case_id, str(e)[:120])
    if speedups:
        log.info("  geometric mean speedup (raw/onto) = %.3f", geometric_mean(speedups))
    if textsql_speedups:
        log.info("  geometric mean speedup (raw/textsql) = %.3f", geometric_mean(textsql_speedups))


async def _timed_call(
    client: httpx.AsyncClient, case: ReadCase, q: ReadQuery, params: param_resolver.ReadParams
) -> tuple[Any, float]:
    t0 = time.perf_counter()
    await _run_special(client, case, q, params)
    return None, time.perf_counter() - t0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ READ DIMENSION ═══")
    print(f"correctness: {summary.pass_n}/{summary.counted} = {summary.correctness_rate:.1%}")
    print(
        f"xfail={summary.xfail_n} xpass(regression met)={summary.xpass_n} error={summary.error_n} skip={summary.skip_n}"
    )
    for r in summary.results:
        perf = f" tax={r.metrics.get('tax_pct', '?')}%" if r.metrics.get("tax_pct") is not None else ""
        tb = r.metrics.get("trivial_baselines", {})
        tb_s = f" trivial={tb}" if tb else ""
        print(f"  {r.outcome.value:7} {r.case_id:8} ({r.elapsed_s:.1f}s){perf}{tb_s} — {r.detail[:90]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
