"""Security harness (DESIGN.md §4.3, dimension 3).

Tests multi-dealership / multi-sales data isolation + permission boundaries.
leak quantified as leak_calls / total_calls (DESIGN.md §4.3).

S1 (Tier2, regression #6 P0): object-type-level read permission — a role
without read on Lead should get 403. The backend builds ActionContext from
X-User-Roles header (MVP principal); read-side enforcement is partial, so S1
is expected to XFAIL until Sprint-3 auth lands.
S2/S3 (Tier1): row-level / dealership isolation via the principal header.
S4 (Tier2): write permission on unassigned leads.
S5 (Tier2): AI-product visibility by role.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import param_resolver
from .base import CaseResult, DimSummary, Outcome, classify

log = logging.getLogger("security_harness")

ONTO = "Marketing"
API_BASE = "http://localhost:8000"
CASE_TIMEOUT = 60.0


@dataclass
class SecCase:
    case_id: str
    tier: int
    description: str
    # (params) -> (headers, request_spec, expectation)
    build: Any
    kind: str = "forbidden"  # forbidden = must NOT see/leak


def _build_s1(p):
    # S1: a role with NO read permission on Lead queries it → expect 403.
    return {
        "headers": {"X-User-Id": "user_no_read", "X-User-Roles": "no-read-role"},
        "request": {
            "method": "POST",
            "path": "/objects/load",
            "json": {
                "object_set": {
                    "object_type_api_name": f"{ONTO}.Lead",
                    "filter": {"type": "eq", "field": "id", "value": p.lead_id},
                },
                "properties": ["id"],
                "limit": 1,
            },
        },
        "expect": "403",
        "leak_check": "status",  # 200 = leak, 403 = enforced
    }


def _build_s2(p):
    # S2: sales A queries sales B's leads (row-level). Expect 403 or empty.
    return {
        "headers": {"X-User-Id": "S00000001", "X-User-Roles": "sales"},
        "request": {
            "method": "POST",
            "path": "/objects/load",
            "json": {
                "object_set": {
                    "object_type_api_name": f"{ONTO}.Lead",
                    "filter": {"type": "eq", "field": "id", "value": p.lead_id},
                },
                "properties": ["id"],
                "limit": 1,
            },
        },
        "expect": "403_or_empty",
        "leak_check": "rows",  # if rows returned for another sales's lead → leak
    }


def _build_s3(p):
    # S3: dealership isolation — sales in store A queries store B's data.
    return {
        "headers": {"X-User-Id": "S00000001", "X-User-Roles": "sales", "X-Workspace-Id": p.store_code},
        "request": {
            "method": "POST",
            "path": "/objects/load",
            "json": {
                "object_set": {
                    "object_type_api_name": f"{ONTO}.Dealership",
                    "filter": {"type": "eq", "field": "storeCode", "value": "D00000002"},
                },
                "properties": ["storeCode"],
                "limit": 1,
            },
        },
        "expect": "403_or_empty",
        "leak_check": "rows",
    }


def _build_s4(p):
    # S4: write permission — sales operates on a lead not assigned to them.
    return {
        "headers": {"X-User-Id": "S00000001", "X-User-Roles": "sales"},
        "request": {
            "method": "POST",
            "path": f"/actions/execute/{ONTO}/Lead/allocateLead",
            "json": {
                "parameters": {"leadId": p.lead_id, "salesConsultantId": "S00000099"},
                "idempotency_key": f"s4-{int(time.time())}",
            },
        },
        "expect": "403",
        "leak_check": "status",
    }


CASES: list[SecCase] = [
    SecCase("S1", 2, "对象类型级读权限 lead 403 (regression #6)", _build_s1),
    SecCase("S2", 1, "行级隔离 销售A查销售B线索", _build_s2),
    SecCase("S3", 1, "门店隔离 销售查他店数据", _build_s3),
    SecCase("S4", 2, "写权限 销售操作未分配线索 403", _build_s4),
]


async def run_case(
    case: SecCase, params: param_resolver.ReadParams, client: httpx.AsyncClient
) -> tuple[CaseResult, bool]:
    """Returns (result, leaked). leaked=True means data escaped the boundary."""
    spec = case.build(params)
    req = spec["request"]
    headers = spec.get("headers", {})
    try:
        if req["method"] == "POST":
            r = await client.post(req["path"], json=req["json"], headers=headers, timeout=CASE_TIMEOUT)
        else:
            r = await client.get(req["path"], headers=headers, timeout=CASE_TIMEOUT)
    except Exception as e:
        return CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR, f"request failed: {e}"), False

    leaked = False
    body = {}
    try:
        body = r.json()
    except Exception:
        pass

    if spec["leak_check"] == "status":
        # 200 when 403 expected = leak; 403/401 = enforced.
        leaked = r.status_code == 200
        passed = not leaked  # enforcement worked
        detail = f"status={r.status_code} (expected {spec['expect']})"
    else:  # rows
        rows = body if isinstance(body, list) else body.get("data", [])
        leaked = isinstance(rows, list) and len(rows) > 0
        passed = not leaked
        detail = (
            f"status={r.status_code} rows={len(rows) if isinstance(rows, list) else '?'} (expected {spec['expect']})"
        )

    res = classify(case.case_id, case.tier, case.kind, passed, detail)
    return res, leaked


async def run_all() -> DimSummary:
    summary = DimSummary(dimension="security")
    params = param_resolver.resolve_read_params()
    leak_calls = 0
    total_calls = 0
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CASE_TIMEOUT + 10) as client:
        for case in CASES:
            log.info("── Security case %s (tier %d) %s ──", case.case_id, case.tier, case.description)

            async def _run():
                return await run_case(case, params, client)

            # run_case returns a tuple; wrap to fit run_with_timeout's CaseResult contract.
            t0 = time.perf_counter()
            try:
                res, leaked = await asyncio.wait_for(run_case(case, params, client), CASE_TIMEOUT)
                res.elapsed_s = round(time.perf_counter() - t0, 2)
                summary.add(res)
                total_calls += 1
                if leaked:
                    leak_calls += 1
                log.info("  → %s (%.1fs) leak=%s: %s", res.outcome.value, res.elapsed_s, leaked, res.detail[:140])
            except TimeoutError:
                r = CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR_TIMEOUT, "exceeded budget")
                summary.add(r)
                log.info("  → ERROR_TIMEOUT")
    summary.results  # noqa
    # Stash leak rate on the first result's metrics for the report.
    if summary.results:
        summary.results[0].metrics["leak_rate"] = leak_calls / total_calls if total_calls else 0.0
        summary.results[0].metrics["leak_calls"] = leak_calls
        summary.results[0].metrics["total_calls"] = total_calls
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ SECURITY DIMENSION ═══")
    print(f"correctness: {summary.pass_n}/{summary.counted} = {summary.correctness_rate:.1%}")
    print(f"xfail={summary.xfail_n} xpass={summary.xpass_n} error={summary.error_n} skip={summary.skip_n}")
    if summary.results:
        m = summary.results[0].metrics
        print(f"leak rate: {m.get('leak_calls', 0)}/{m.get('total_calls', 0)} = {m.get('leak_rate', 0):.1%}")
    for r in summary.results:
        print(f"  {r.outcome.value:7} {r.case_id:4} ({r.elapsed_s:.1f}s) — {r.detail[:100]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
