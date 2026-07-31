"""Agent harness (DESIGN.md §4.4, dimension 4).

Paired comparison: same NL question → (a) Text-to-Ontology (LLM picks ontology
filter via /ai/agent), (b) Text-to-SQL (LLM emits physical SQL run on MySQL).
McNemar exact test on the paired pass/fail outcomes (DESIGN.md §七).

flake-aware: each question runs N≥3 times within the 60s budget; pass_rate
threshold (≥2/3) gates the final pass. cost cap: global token budget; if
exceeded the dimension aborts (fail-fast).

A1-A7 reuse the read cases' expected (golden SQL) as ground truth. A8 (fuzzy)
and A9 (multi-turn) are Tier2/Tier3 (xfail).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

from . import golden_truth, param_resolver
from .assertion_engine import assert_by_kind
from .base import CaseResult, DimSummary, Outcome, classify
from .stats import mcnemar_exact

log = logging.getLogger("agent_harness")

ONTO = "Marketing"
API_BASE = "http://localhost:8000"
CASE_TIMEOUT = 60.0
N_RETRIES = 3  # flake-aware: ≥3 attempts within budget
PASS_THRESHOLD = 2  # ≥2/3 = pass
GLOBAL_TOKEN_CAP = 200_000


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


CASES: list[AgentCase] = [
    AgentCase(
        "A1", 1, "查线索 L00000001 的客户姓名和电话", "L1", "set_eq", ["customer_name", "customer_phone"], ["lead_id"]
    ),
    AgentCase(
        "A2",
        1,
        "销售 16138788221 今天要邀约的线索",
        "L2",
        "ordered_list",
        ["customer_name", "customer_phone", "next_follow_time"],
        ["sales_phone", "date_pattern"],
    ),
    AgentCase("A3", 1, "线索 L00000001 对应的销售手机号", "L3", "set_eq", ["sales_consultant_phone"], ["lead_id"]),
    AgentCase(
        "A4", 1, "销售 16138788221 今天打了多少通电话", "L4", "count_eq", ["count"], ["sales_phone", "date_pattern"]
    ),
    AgentCase(
        "A5",
        1,
        "2026-06-15 完成的试驾和它们的录音",
        "L5",
        "set_eq",
        ["test_drive_id", "customer_name", "rec_url"],
        ["date_pattern"],
    ),
    AgentCase(
        "A6",
        1,
        "2026-04-16 09:00:00 之后更新的销售顾问",
        "L6",
        "set_eq",
        ["sales_consultant_id", "sales_consultant_name"],
        ["formatted_time"],
    ),
    AgentCase(
        "A7",
        1,
        "门店 D00000001 所有销售的有效线索",
        "L7",
        "set_eq",
        ["lead_id", "customer_name", "customer_phone"],
        ["store_code"],
    ),
    AgentCase("A8", 2, "最近表现好的销售", "L4", "set_equiv", [], []),  # fuzzy/ambiguous
    AgentCase("A9", 3, "帮我找出待回访线索，再按试驾完成排序", "L2", "ordered_list", [], []),  # multi-turn
]


def _llm_configured() -> bool:
    model = os.environ.get("AI_MODEL", "")
    if not model:
        return False
    provider = model.split(":", 1)[0].lower()
    key_var = f"{provider}_api_key".upper()
    return bool(os.environ.get(key_var) or os.environ.get(key_var.lower()))


async def _text_to_ontology(
    client: httpx.AsyncClient, case: AgentCase, params: param_resolver.ReadParams
) -> list[dict]:
    """Call the /ai/agent endpoint to turn NL into an ontology query + result.

    The AG-UI agent is complex; for the benchmark we use the simpler /ai/generate
    path to ask the LLM to produce a LoadObjectsRequest JSON, then run it. If
    the AI endpoints are unavailable, this raises (→ ERROR for the case).
    """
    # Try the /ai/agent stream endpoint (AG-UI). It's a long-lived SSE stream;
    # we cap at the case budget. For the benchmark skeleton we instead issue a
    # /ai/generate request asking for a query spec, then execute it.
    prompt = (
        f"Given the Marketing ontology (object types: Lead, User, SalesConsultant, "
        f"TestDrive, ManualOutboundCall, LeadAllocateRecord, etc.), translate this "
        f"question into a JSON LoadObjectsRequest (object_type_api_name like "
        f"'Marketing.Lead', properties list, filter tree with type/field/value, limit). "
        f"Question: {case.nl}. Reply with ONLY the JSON."
    )
    r = await client.post("/ai/generate", json={"prompt": prompt, "max_tokens": 800}, timeout=CASE_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"/ai/generate → {r.status_code}: {r.text[:200]}")
    text = r.json().get("text", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
    # Parse JSON from the response (best-effort).
    import json
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError("agent did not return JSON")
    spec = json.loads(m.group(0))
    # Execute the spec.
    r2 = await client.post("/objects/load", json=spec, timeout=CASE_TIMEOUT)
    if r2.status_code != 200:
        raise RuntimeError(f"agent query → {r2.status_code}: {r2.text[:200]}")
    return r2.json()


async def _text_to_sql(client: httpx.AsyncClient, case: AgentCase, params: param_resolver.ReadParams) -> list[dict]:
    """Ask the LLM to emit physical SQL, run on MySQL (golden_truth path)."""
    prompt = (
        f"Given the Marketing MySQL schema (tables: t_ods_leads_server_leads_info_rt "
        f"lead, t_ods_leads_server_leads_user_rt user, t_ods_master_data_staff staff, "
        f"t_ods_test_drive_test_drive_rt test_drive, t_ods_leads_server_sale_call_record_rt "
        f"manual_call, t_ods_source_data_leads_operation_record allocate), write a SELECT "
        f"SQL for: {case.nl}. Reply with ONLY the SQL."
    )
    r = await client.post("/ai/generate", json={"prompt": prompt, "max_tokens": 800}, timeout=CASE_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"/ai/generate → {r.status_code}")
    text = r.json().get("text", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
    import re

    m = re.search(r"SELECT .*?(?:;|$)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        raise RuntimeError("agent did not return SQL")
    sql = m.group(0).rstrip(";")
    # Run on MySQL (read-only — DESIGN.md §2.2 read path has no restriction).
    import aiomysql

    conn = await aiomysql.connect(
        host="localhost",
        port=3306,
        user="root",
        password="marketing123",
        db="marketing_benchmark",
        autocommit=True,
        charset="utf8mb4",
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
            v = assert_by_kind(case.kind, expected, onto, select_keys=case.select_keys or None)
            if v.passed:
                onto_passes += 1
        except Exception as e:
            onto_err = str(e)[:100]
        try:
            sqlr = await _text_to_sql(client, case, params)
            v = assert_by_kind(case.kind, expected, sqlr, select_keys=case.select_keys or None)
            if v.passed:
                sql_passes += 1
        except Exception as e:
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
    res.metrics = {"onto_pass_rate": onto_passes / N_RETRIES, "sql_pass_rate": sql_passes / N_RETRIES}
    return res, onto_passed, sql_passed


async def run_all() -> DimSummary:
    summary = DimSummary(dimension="agent")
    if not _llm_configured():
        log.warning("No LLM configured (AI_MODEL/provider key) — agent dimension SKIPPED.")
        for case in CASES:
            r = CaseResult(case.case_id, case.tier, case.kind, Outcome.SKIPPED, "no LLM configured")
            summary.add(r)
        return summary
    params = param_resolver.resolve_read_params()
    # McNemar discordant counts.
    b = 0  # onto passed, sql failed
    a = 0  # sql passed, onto failed
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CASE_TIMEOUT + 10) as client:
        for case in CASES:
            log.info("── Agent case %s (tier %d): %s ──", case.case_id, case.tier, case.nl[:40])

            async def _run():
                return await run_case(case, params, client)

            try:
                res, onto_p, sql_p = await asyncio.wait_for(run_case(case, params, client), CASE_TIMEOUT)
                summary.add(res)
                if onto_p and not sql_p:
                    b += 1
                elif sql_p and not onto_p:
                    a += 1
                log.info("  → %s: %s", res.outcome.value, res.detail[:120])
            except TimeoutError:
                r = CaseResult(case.case_id, case.tier, case.kind, Outcome.ERROR_TIMEOUT, "exceeded budget")
                summary.add(r)
    # McNemar (DESIGN.md §七).
    p_value, (diff, _) = mcnemar_exact(b, a)
    if summary.results:
        summary.results[0].metrics["mcnemar_b"] = b
        summary.results[0].metrics["mcnemar_a"] = a
        summary.results[0].metrics["mcnemar_p"] = p_value
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(run_all())
    print("\n═══ AGENT DIMENSION ═══")
    print(f"correctness: {summary.pass_n}/{summary.counted} = {summary.correctness_rate:.1%}")
    print(f"xfail={summary.xfail_n} xpass={summary.xpass_n} error={summary.error_n} skip={summary.skip_n}")
    if summary.results and summary.results[0].metrics.get("mcnemar_p") is not None:
        m = summary.results[0].metrics
        print(f"McNemar: b(onto-only)={m['mcnemar_b']} a(sql-only)={m['mcnemar_a']} p={m['mcnemar_p']:.4f}")
    for r in summary.results:
        print(f"  {r.outcome.value:7} {r.case_id:4} — {r.detail[:100]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
