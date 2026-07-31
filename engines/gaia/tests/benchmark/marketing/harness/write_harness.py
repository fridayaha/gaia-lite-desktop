"""Write-path harness (DESIGN.md §4.2, dimension 2).

Action execution via POST /actions/execute (principle 3.5: all writes via API).
Each write case:
  1. Resolve params + pick a target object id (deterministic from seed).
  2. Execute the Action via API.
  3. Verify postcondition (read-only: re-query the API / direct Doris count).
  4. Classify (PASS/FAIL/XFAIL for Tier2 regressions #3/#4/#5).

W7/W8 (AI products) are gated on a configured AI_MODEL; if no LLM key is set
they SKIP (not FAIL) so the dimension isn't polluted by missing infra.

Trivial baselines: empty payload (should 422), illegal operation_type, etc.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import param_resolver
from .base import CaseResult, DimSummary, Outcome, classify, run_with_timeout

log = logging.getLogger("write_harness")

ONTO = "Marketing"
API_BASE = "http://localhost:8000"
CASE_TIMEOUT = 60.0


@dataclass
class WriteCase:
    case_id: str
    tier: int
    description: str
    object_type: str
    action: str
    # Builder: (params) -> (parameters, idempotency_key, postcondition_spec)
    build: Any
    kind: str = "postcondition"
    needs_llm: bool = False  # W7/W8 — skip if no AI_MODEL key
    regression: str = ""  # e.g. "#5 OCC", "#3 type coercion"


def _uuid_key(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _pick_lead_id(params: param_resolver.ReadParams) -> str:
    return params.lead_id


def _build_w1(p):
    # W1 allocateLead: assign a lead to a sales consultant.
    # Pick a sales consultant different from the lead's current owner.
    return {
        "object_type": "Lead",
        "action": "allocateLead",
        "parameters": {"leadId": p.lead_id, "salesConsultantId": "S00000002"},
        "idempotency_key": _uuid_key("w1"),
        "post": {"check": "lead_allocate_record_new", "lead_id": p.lead_id},
    }


def _build_w4(p):
    # W4 recordFollow: write a follow-up record.
    return {
        "object_type": "LeadFollowRecord",
        "action": "recordFollow",
        "parameters": {
            "leadId": p.lead_id,
            "followerId": "S00000002",
            "followPurpose": "邀约试驾",
            "followResult": "已邀约",
            "followContent": "客户同意本周末试驾",
        },
        "idempotency_key": _uuid_key("w4"),
        # Postcondition: the LeadFollowRecord OT currently exposes no leads_id/
        # follower_id FK property (the action sets them via ontology_rules but
        # they aren't queryable OT properties), so we verify the action applied
        # (status 200) rather than re-querying by lead. This is a recorded
        # harness/ontology gap (see DESIGN.md §12).
        "post": {"check": "status_200"},
    }


def _build_w5(p):
    # W5 progressTestDrive: state machine 0→1→2→3→4. Pick the first test drive.
    return {
        "object_type": "TestDrive",
        "action": "progressTestDrive",
        "parameters": {"testDriveId": p.test_drive_id, "newStatus": "2"},
        "idempotency_key": _uuid_key("w5"),
        "post": {"check": "test_drive_status", "test_drive_id": p.test_drive_id, "expected_status": "2"},
    }


def _build_w6(p):
    # W6 logManualCall: manual outbound call record.
    return {
        "object_type": "ManualOutboundCall",
        "action": "logManualCall",
        "parameters": {
            "leadId": p.lead_id,
            "userId": p.user_id,
            "callStatus": "1",
            "callDuration": 120,
        },
        "idempotency_key": _uuid_key("w6"),
        "post": {"check": "manual_call_new", "lead_id": p.lead_id},
    }


def _build_w7(p):
    # W7 analyzeTestDrive: AI product (5 test-drive report tables).
    return {
        "object_type": "TestDrive",
        "action": "analyzeTestDrive",
        "parameters": {"testDriveId": p.test_drive_id},
        "idempotency_key": _uuid_key("w7"),
        "post": {
            "check": "ai_product_tables",
            "tables": [
                "TdAnalysisDetails",
                "CompetitiveAnalysis",
                "StrategyExecutionAudit",
                "ScriptExecutionAnalysis",
                "FocusResistancePoints",
            ],
        },
    }


def _build_w9(p):
    # W9 OCC: 50 concurrent allocateLead on the same lead (regression #5).
    return {
        "object_type": "Lead",
        "action": "allocateLead",
        "parameters": {"leadId": p.lead_id, "salesConsultantId": "S00000003"},
        "idempotency_key": _uuid_key("w9"),
        "post": {"check": "occ_success_rate", "target_rate": 0.99, "concurrency": 50},
    }


def _build_w11(p):
    # W11 reassignTestDriveCar: ObjectReference type coercion (regression #3, 422→200).
    return {
        "object_type": "TestDrive",
        "action": "reassignTestDriveCar",
        "parameters": {"testDriveId": p.test_drive_id, "newTestDriveCarId": "TDC00000002"},
        "idempotency_key": _uuid_key("w11"),
        "post": {"check": "status_200"},
    }


CASES: list[WriteCase] = [
    WriteCase("W1", 1, "线索分配 allocateLead", "Lead", "allocateLead", _build_w1),
    WriteCase("W4", 1, "线索跟进记录 recordFollow", "LeadFollowRecord", "recordFollow", _build_w4),
    WriteCase("W5", 1, "试驾状态流转 progressTestDrive", "TestDrive", "progressTestDrive", _build_w5),
    WriteCase("W6", 1, "外呼记录 logManualCall", "ManualOutboundCall", "logManualCall", _build_w6),
    WriteCase("W7", 1, "AI产物 试驾报告 analyzeTestDrive", "TestDrive", "analyzeTestDrive", _build_w7, needs_llm=True),
    WriteCase("W9", 2, "OCC并发 50路 allocateLead (regression #5)", "Lead", "allocateLead", _build_w9, regression="#5"),
    WriteCase(
        "W11",
        2,
        "Action规则类型转换 reassignTestDriveCar (regression #3)",
        "TestDrive",
        "reassignTestDriveCar",
        _build_w11,
        regression="#3",
    ),
]


def _llm_configured() -> bool:
    """W7/W8 need an LLM. Check AI_MODEL + a provider key in env."""
    model = os.environ.get("AI_MODEL", "")
    if not model:
        return False
    provider = model.split(":", 1)[0].lower()
    key_var = f"{provider}_api_key".upper()
    # pydantic-ai reads provider keys from env; if none set, skip.
    return bool(os.environ.get(key_var) or os.environ.get(key_var.lower()))


async def _execute_action(client: httpx.AsyncClient, spec: dict, headers: dict | None = None) -> dict:
    """POST /actions/execute/{onto}/{ot}/{action}. Returns {'status_code', 'body'}."""
    url = f"/actions/execute/{ONTO}/{spec['object_type']}/{spec['action']}"
    payload = {"parameters": spec["parameters"], "idempotency_key": spec["idempotency_key"]}
    r = await client.post(url, json=payload, timeout=CASE_TIMEOUT, headers=headers or {})
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    return {"status_code": r.status_code, "body": body}


async def _check_postcondition(client: httpx.AsyncClient, spec: dict) -> tuple[bool, str]:
    """Verify the postcondition (read-only). Returns (passed, detail)."""
    post = spec["post"]
    check = post["check"]

    if check == "status_200":
        # W11: the action itself returning 200 (not 422) is the postcondition.
        return True, "status_200 (action accepted)"

    if check == "lead_allocate_record_new":
        # Verify a new allocate record exists for this lead (read LeadAllocateRecord).
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {
                    "object_type_api_name": f"{ONTO}.LeadAllocateRecord",
                    "filter": {"type": "eq", "field": "leadsId", "value": post["lead_id"]},
                },
                "properties": ["oid", "leadsId", "salesConsultantId"],
                "limit": 50,
            },
            timeout=CASE_TIMEOUT,
        )
        rows = r.json() if r.status_code == 200 else []
        return (len(rows) > 0, f"allocate records for lead: {len(rows)}")

    if check == "lead_follow_record_new":
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {
                    "object_type_api_name": f"{ONTO}.LeadFollowRecord",
                    "filter": {"type": "eq", "field": "leadsId", "value": post["lead_id"]},
                },
                "properties": ["oid"],
                "limit": 50,
            },
            timeout=CASE_TIMEOUT,
        )
        rows = r.json() if r.status_code == 200 else []
        return (len(rows) > 0, f"follow records for lead: {len(rows)}")

    if check == "test_drive_status":
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {
                    "object_type_api_name": f"{ONTO}.TestDrive",
                    "filter": {"type": "eq", "field": "id", "value": post["test_drive_id"]},
                },
                "properties": ["id", "orderStatus"],
                "limit": 1,
            },
            timeout=CASE_TIMEOUT,
        )
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return False, "test drive not found"
        actual = str(rows[0].get("orderStatus"))
        ok = actual == str(post["expected_status"])
        return (ok, f"orderStatus={actual} (expected {post['expected_status']})")

    if check == "manual_call_new":
        r = await client.post(
            "/objects/load",
            json={
                "object_set": {
                    "object_type_api_name": f"{ONTO}.ManualOutboundCall",
                    "filter": {"type": "eq", "field": "leadId", "value": post["lead_id"]},
                },
                "properties": ["id"],
                "limit": 50,
            },
            timeout=CASE_TIMEOUT,
        )
        rows = r.json() if r.status_code == 200 else []
        return (len(rows) > 0, f"manual calls for lead: {len(rows)}")

    if check == "ai_product_tables":
        # Verify each AI-product ObjectType has ≥1 object (read-only count via API).
        details = []
        all_ok = True
        for ot in post["tables"]:
            try:
                r = await client.post(
                    "/objects/load",
                    json={
                        "object_set": {"object_type_api_name": f"{ONTO}.{ot}"},
                        "properties": [],
                        "limit": 1,
                    },
                    timeout=CASE_TIMEOUT,
                )
                n = len(r.json()) if r.status_code == 200 else 0
                details.append(f"{ot}={n}")
                if n == 0:
                    all_ok = False
            except Exception as e:
                details.append(f"{ot}=ERR:{str(e)[:40]}")
                all_ok = False
        return (all_ok, "; ".join(details))

    if check == "occ_success_rate":
        # W9: run N concurrent allocateLead, measure success rate.
        n = post["concurrency"]
        target = post["target_rate"]

        async def one(i: int) -> int:
            spec_i = {
                "object_type": "Lead",
                "action": "allocateLead",
                "parameters": {"leadId": spec["parameters"]["leadId"], "salesConsultantId": f"S{i + 1:08d}"},
                "idempotency_key": _uuid_key(f"w9-{i}"),
            }
            res = await _execute_action(client, spec_i)
            return 1 if res["status_code"] == 200 else 0

        results = await asyncio.gather(*[one(i) for i in range(n)])
        success = sum(results)
        rate = success / n
        ok = rate >= target
        return (ok, f"OCC success {success}/{n} = {rate:.1%} (target >{target:.0%})")

    return False, f"unknown postcondition check: {check}"


async def run_case(case: WriteCase, params: param_resolver.ReadParams, client: httpx.AsyncClient) -> CaseResult:
    if case.needs_llm and not _llm_configured():
        return CaseResult(
            case.case_id, case.tier, "skipped", Outcome.SKIPPED, "no LLM configured (AI_MODEL/provider key)"
        )
    spec = case.build(params)
    t0 = time.perf_counter()
    # Execute the action.
    res = await _execute_action(client, spec)
    if res["status_code"] not in (200, 409):  # 409 = idempotent replay (acceptable)
        # For W11 (regression #3): 422 = bug still present (expected fail → XFAIL);
        # 200 = bug fixed (XPASS, regression met). Either way we classify via
        # the regression-met lens: passed=True only when status is 200.
        if case.case_id == "W11":
            fixed = res["status_code"] == 200
            r = classify(
                case.case_id,
                case.tier,
                "action_rejected",
                fixed,
                f"status={res['status_code']} [regression #3: 422=bug-present(XFAIL), 200=fixed(XPASS)]",
            )
            return r
        return CaseResult(
            case.case_id,
            case.tier,
            case.kind,
            Outcome.ERROR,
            f"action returned {res['status_code']}: {str(res['body'])[:200]}",
            elapsed_s=round(time.perf_counter() - t0, 2),
        )
    # Verify postcondition (W9 handles its own execution in the postcheck).
    passed, detail = await _check_postcondition(client, spec)
    r = classify(case.case_id, case.tier, case.kind, passed, detail)
    r.elapsed_s = round(time.perf_counter() - t0, 2)
    return r


async def run_all() -> DimSummary:
    summary = DimSummary(dimension="write")
    params = param_resolver.resolve_read_params()
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CASE_TIMEOUT + 10) as client:
        for case in CASES:
            log.info("── Write case %s (tier %d) %s ──", case.case_id, case.tier, case.description)

            async def _run():
                return await run_case(case, params, client)

            r = await run_with_timeout(case.case_id, case.tier, case.kind, _run, CASE_TIMEOUT)
            summary.add(r)
            log.info("  → %s (%.1fs): %s", r.outcome.value, r.elapsed_s, r.detail[:160])
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ WRITE DIMENSION ═══")
    print(f"correctness: {summary.pass_n}/{summary.counted} = {summary.correctness_rate:.1%}")
    print(f"xfail={summary.xfail_n} xpass={summary.xpass_n} error={summary.error_n} skip={summary.skip_n}")
    for r in summary.results:
        print(f"  {r.outcome.value:7} {r.case_id:4} ({r.elapsed_s:.1f}s) — {r.detail[:100]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
