"""DVP Agent harness (DESIGN.md §4.2, dimension 2).

Paired comparison: same NL question →
  (a) Text-to-Ontology: LLM emits logical SQL → /objects/textsql (Trino federation)
  (b) Text-to-SQL:      LLM emits physical SQL → run on MySQL directly
McNemar exact test on the paired pass/fail outcomes (DESIGN.md §七).

flake-aware: each question runs N≥3 times within the 60s budget; pass_rate
threshold (≥2/3) gates the final pass.

收编适配：text_to_ontology 改为生成 logical SQL 调 /objects/textsql（原
/objects/load + LoadObjectsRequest 已在 PR 4 删除，统一走编译路径）。

A1-A7 reuse the read cases' expected (golden SQL) as ground truth. A8 (fuzzy)
and A9 (multi-turn) are Tier2/Tier3 (XFAIL).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

# Load .env so AI_MODEL + provider keys are visible to _llm_configured().
load_dotenv()

from . import golden_truth, param_resolver  # noqa: E402
from .assertion_engine import assert_by_kind  # noqa: E402
from .base import CaseResult, DimSummary, Outcome, classify  # noqa: E402
from .stats import mcnemar_exact  # noqa: E402

log = logging.getLogger("agent_harness")

ONTO = "DVP"
API_BASE = "http://localhost:8000"
CASE_TIMEOUT = 200.0  # per-case budget (2 modes × 3 retries × ~30s LLM call)
N_RETRIES = 3  # flake-aware: ≥3 attempts within budget
PASS_THRESHOLD = 2  # ≥2/3 = pass

# DVP MySQL connection (same container as read harness golden truth).
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = "marketing123"
MYSQL_DB = "dvp_benchmark"


@dataclass
class AgentCase:
    case_id: str
    tier: int
    nl: str  # natural-language question
    # Ground-truth case_id (read case) + kind + select_keys for scoring.
    ground_case: str
    kind: str
    select_keys: list[str] = field(default_factory=list)
    params_needed: list[str] = field(default_factory=list)


# A1-A7 mirror read L1-L7/L9-L11 (NL versions). A8/A9 are tier 2/3 XFAIL.
CASES: list[AgentCase] = [
    AgentCase(
        "A1", 1, "查项目 P2024001 的项目名称和负责人", "L1", "set_eq",
        ["projectName", "projectManager"], ["project_code"],
    ),
    AgentCase(
        "A2", 1, "项目 P2024001 下所有车型按开发等级排序", "L2", "ordered_list",
        ["vehicleCode", "devTier"], ["project_code"],
    ),
    AgentCase(
        "A3", 1, "变化点 CP-000001 影响哪个项目令号", "L3", "set_eq",
        ["projectCode"], ["change_point_id"],
    ),
    AgentCase(
        "A4", 1, "项目 P2024001 下各工况有多少试验项", "L4", "count_eq",
        ["detailConditionCode", "count"], ["project_code"],
    ),
    AgentCase(
        "A5", 1, "列出没有绑定规范的试验项", "L5", "null_allowed",
        ["testItemId", "testItemName"], ["detail_condition_code"],
    ),
    AgentCase(
        "A6", 1, "项目 P2024001 最近一周更新的零部件", "L6", "set_eq",
        ["componentName"], ["project_code", "since_time"],
    ),
    AgentCase(
        "A7", 1, "正面碰撞工况下待执行的试验项", "L7", "set_eq",
        ["testItemId", "testItemName"], ["condition_type", "status"],
    ),
    AgentCase("A8", 2, "最近变更较多的零部件", "L6", "set_eq", [], ["project_code", "since_time"]),  # fuzzy/ambiguous
    AgentCase("A9", 3, "找出待执行试验项，再按工况分组统计", "L4", "count_eq", [], ["project_code"]),  # multi-turn
]

# DVP object types exposed to the LLM (text-to-ontology mode prompt context).
# Includes key property api_names per OT so the LLM emits valid logical SQL
# (the compiler rejects unknown columns). Curated from the ontology seed.
DVP_OBJECT_TYPES = [
    "ProjectBase", "ProjectVehicle", "VehicleBody", "FrontStructure",
    "SideStructure", "RearStructure", "ChassisStructure", "ExteriorDesign",
    "Component", "ChangePointEntity", "DvpDesign", "ExperimentItemRound",
    "OperCondition", "FrontCollision", "RearCollision", "SideCollision",
    "PedestrianProtect", "TestItem", "Spec", "Dimension", "LmsTargetIteration",
    "LmsTargetDimension", "VehicleSyncRecord", "ScheduleTestItem",
]

# Key properties (api_name, camelCase) per OT — helps the LLM emit valid columns.
OT_PROPERTIES: dict[str, list[str]] = {
    "ProjectBase": ["projectCode", "projectName", "brand", "projectType",
                   "devTier", "lifecycleState", "projectStatus", "managerName"],
    "ProjectVehicle": ["vehicleCode", "projectCode", "devTier", "driveType", "powerType", "targetMarket"],
    "VehicleBody": ["bodyCode", "vehicleCode", "bodyName"],
    "Component": ["componentId", "componentName", "componentCategory", "bodyCode", "updateTime"],
    "ChangePointEntity": ["changePointId", "changeDegree", "changeDescription", "componentId"],
    "DvpDesign": ["dvpCode", "projectCode", "planEndTime", "planStartTime"],
    "ExperimentItemRound": ["roundCode", "projectCode", "dvpCode"],
    "OperCondition": ["conditionCode", "conditionName", "roundCode"],
    "FrontCollision": ["detailConditionCode", "detailConditionName", "conditionType", "conditionCode"],
    "TestItem": ["testItemId", "testItemName", "status", "detailConditionCode", "createTime", "specCode"],
    "Spec": ["specCode", "specName", "status", "updateTime"],
    "Dimension": ["dimensionId", "dimensionName"],
    "LmsTargetIteration": ["iterationId", "dimensionId", "iterationVersion", "iterationDate", "iterationThreshold"],
}

# DVP MySQL tables exposed to the LLM (text-to-SQL mode prompt context).
DVP_MYSQL_TABLES = [
    "t_project_base", "t_project_vehicle", "t_vehicle_body",
    "t_front_structure", "t_side_structure", "t_rear_structure",
    "t_chassis_structure", "t_exterior_design", "t_component",
    "t_change_point_entity", "t_dvp_design", "t_experiment_item_round",
    "t_oper_condition", "t_oper_condition_detail", "t_test_item", "t_spec",
    "t_dimension", "t_lms_target_iteration", "t_lms_target_dimension",
    "t_vehicle_sync_record", "t_schedule_test_item",
]


def _llm_configured() -> bool:
    model = os.environ.get("AI_MODEL", "")
    if not model:
        return False
    provider = model.split(":", 1)[0].lower()
    # provider key env var convention: <PROVIDER>_API_KEY
    key_var = f"{provider}_api_key".upper()
    return bool(os.environ.get(key_var))


async def _text_to_ontology(
    client: httpx.AsyncClient, case: AgentCase, params: param_resolver.ReadParams
) -> list[dict]:
    """Text-to-Ontology: LLM emits logical SQL → /objects/textsql (Trino federation).

    收编后路径：不再生成 LoadObjectsRequest（已删），直接生成 logical SQL
    （用 OT api_name 当表名/列名，compiler 映射物理列 + 三段名 Trino 联邦）。
    """
    ot_schema = "; ".join(f"{ot}({', '.join(props)})" for ot, props in OT_PROPERTIES.items())
    prompt = (
        f"Given the DVP ontology, translate this question into a single SELECT logical SQL. "
        f"Use the ObjectType api_name as the table name and the camelCase property api_names as columns. "
        f"The compiler rewrites to physical tables, so do NOT use physical table/column names. "
        f"Object types and their properties: {ot_schema}. "
        f"Question: {case.nl}. Reply with ONLY the SQL, no explanation."
    )
    r = await client.post(
        "/ai/generate", json={"prompt": prompt, "max_tokens": 800}, timeout=CASE_TIMEOUT
    )
    if r.status_code != 200:
        raise RuntimeError(f"/ai/generate → {r.status_code}: {r.text[:200]}")
    text = (
        r.json().get("text", "")
        if r.headers.get("content-type", "").startswith("application/json")
        else r.text
    )
    m = re.search(r"SELECT .*?(?:;|$)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        raise RuntimeError("agent did not return SQL")
    sql = m.group(0).rstrip(";").strip()
    # Pick an object type from the SQL (first FROM target) for the route.
    from_match = re.search(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
    ot_api = from_match.group(1) if from_match else case.ground_case.replace("L", "ProjectBase")
    payload = {"object_type_api_name": f"{ONTO}.{ot_api}", "logical_sql": sql}
    r2 = await client.post("/objects/textsql", json=payload, timeout=CASE_TIMEOUT)
    if r2.status_code != 200:
        raise RuntimeError(f"/objects/textsql → {r2.status_code}: {r2.text[:200]}")
    return r2.json()


async def _text_to_sql(
    client: httpx.AsyncClient, case: AgentCase, params: param_resolver.ReadParams
) -> list[dict]:
    """Text-to-SQL: LLM emits physical SQL, run on MySQL directly."""
    # Give the LLM the snake_case physical columns (mirror of OT_PROPERTIES).
    table_cols = (
        "t_project_base(project_code, project_name, manager_name); "
        "t_project_vehicle(vehicle_code, project_code, dev_tier); "
        "t_vehicle_body(body_code, vehicle_code); "
        "t_component(component_id, component_name, body_code, update_time); "
        "t_change_point_entity(change_point_id, change_degree, component_id); "
        "t_dvp_design(dvp_code, project_code, plan_end_time); "
        "t_experiment_item_round(round_code, project_code, dvp_code); "
        "t_oper_condition(condition_code, condition_name, round_code); "
        "t_oper_condition_detail(detail_condition_code, detail_condition_name, condition_type, condition_code); "
        "t_test_item(test_item_id, test_item_name, status, detail_condition_code, create_time, spec_code); "
        "t_spec(spec_code, spec_name, status, update_time); "
        "t_dimension(dimension_id, dimension_name); "
        "t_lms_target_iteration(iteration_id, dimension_id, iteration_version, iteration_date)"
    )
    prompt = (
        f"Given the DVP MySQL schema (tables and key columns: {table_cols}), "
        f"write a SELECT SQL for: {case.nl}. Use snake_case column names. "
        f"Reply with ONLY the SQL, no explanation."
    )
    r = await client.post(
        "/ai/generate", json={"prompt": prompt, "max_tokens": 800}, timeout=CASE_TIMEOUT
    )
    if r.status_code != 200:
        raise RuntimeError(f"/ai/generate → {r.status_code}")
    text = (
        r.json().get("text", "")
        if r.headers.get("content-type", "").startswith("application/json")
        else r.text
    )
    m = re.search(r"SELECT .*?(?:;|$)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        raise RuntimeError("agent did not return SQL")
    sql = m.group(0).rstrip(";").strip()
    # Run on MySQL (read-only).
    import aiomysql

    conn = await aiomysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASSWORD, db=MYSQL_DB, autocommit=True, charset="utf8mb4",
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql.replace("%", "%%"))
            return [dict(r) for r in await cur.fetchall()]
    finally:
        conn.close()


async def _ground_truth(case: AgentCase, params: param_resolver.ReadParams) -> list[dict]:
    """The golden SQL result for this case (from expected_sql)."""
    sql_params = {k: getattr(params, k) for k in case.params_needed}
    return await golden_truth.run_expected(case.ground_case, sql_params)


async def run_case(
    case: AgentCase, params: param_resolver.ReadParams, client: httpx.AsyncClient
) -> tuple[CaseResult, bool, bool]:
    """Returns (result, onto_passed, sql_passed) for McNemar pairing."""
    expected = await _ground_truth(case, params)
    onto_passes = 0
    sql_passes = 0
    onto_err = sql_err = None
    for _ in range(N_RETRIES):
        try:
            onto = await _text_to_ontology(client, case, params)
            v = assert_by_kind(
                case.kind, expected, onto, select_keys=case.select_keys or None
            )
            if v.passed:
                onto_passes += 1
        except Exception as e:  # noqa: BLE001
            onto_err = str(e)[:100]
        try:
            sqlr = await _text_to_sql(client, case, params)
            v = assert_by_kind(
                case.kind, expected, sqlr, select_keys=case.select_keys or None
            )
            if v.passed:
                sql_passes += 1
        except Exception as e:  # noqa: BLE001
            sql_err = str(e)[:100]
    onto_passed = onto_passes >= PASS_THRESHOLD
    sql_passed = sql_passes >= PASS_THRESHOLD
    detail = f"onto={onto_passes}/{N_RETRIES} sql={sql_passes}/{N_RETRIES}"
    if onto_err:
        detail += f" onto_err={onto_err[:40]}"
    if sql_err:
        detail += f" sql_err={sql_err[:40]}"
    # The case "passes" if the ontology mode works (that's the system under test).
    res = classify(case.case_id, case.tier, case.kind, onto_passed, detail)
    res.metrics = {
        "onto_pass_rate": onto_passes / N_RETRIES,
        "sql_pass_rate": sql_passes / N_RETRIES,
    }
    return res, onto_passed, sql_passed


async def run_all() -> DimSummary:
    summary = DimSummary(dimension="agent")
    if not _llm_configured():
        log.warning("No LLM configured (AI_MODEL/provider key) — agent dimension SKIPPED.")
        for case in CASES:
            r = CaseResult(
                case.case_id, case.tier, case.kind, Outcome.SKIPPED, "no LLM configured"
            )
            summary.add(r)
        return summary
    params = param_resolver.resolve_read_params()
    # McNemar discordant counts.
    b = 0  # onto passed, sql failed
    a = 0  # sql passed, onto failed
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CASE_TIMEOUT + 10) as client:
        for case in CASES:
            log.info("── Agent case %s (tier %d): %s ──", case.case_id, case.tier, case.nl[:40])
            try:
                res, onto_p, sql_p = await asyncio.wait_for(
                    run_case(case, params, client), CASE_TIMEOUT
                )
                summary.add(res)
                if onto_p and not sql_p:
                    b += 1
                elif sql_p and not onto_p:
                    a += 1
                log.info("  → %s: %s", res.outcome.value, res.detail[:120])
            except TimeoutError:
                r = CaseResult(
                    case.case_id, case.tier, case.kind, Outcome.ERROR_TIMEOUT, "exceeded budget"
                )
                summary.add(r)
    # McNemar (DESIGN.md §七).
    p_value, (diff, _) = mcnemar_exact(b, a)
    if summary.results:
        summary.results[0].metrics["mcnemar_b"] = b
        summary.results[0].metrics["mcnemar_a"] = a
        summary.results[0].metrics["mcnemar_p"] = p_value
    return summary


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="DVP Agent dimension harness")
    ap.add_argument("--json", metavar="PATH", help="write results JSON to PATH")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ DVP Agent dimension ═══")
    print(
        f"  total={summary.total} PASS={summary.pass_n} FAIL={summary.fail_n} "
        f"XFAIL={summary.xfail_n} XPASS={summary.xpass_n} ERROR={summary.error_n} "
        f"SKIP={summary.skip_n}"
    )
    counted = summary.counted
    if counted:
        print(f"  correctness: {summary.pass_n}/{counted} = {summary.correctness_rate:.1%}")
    if summary.results and summary.results[0].metrics.get("mcnemar_p") is not None:
        m = summary.results[0].metrics
        print(
            f"  McNemar: b(onto-only)={m['mcnemar_b']} a(sql-only)={m['mcnemar_a']} "
            f"p={m['mcnemar_p']:.4f}"
        )
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
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"  results JSON → {out}")
    return 0 if summary.fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
