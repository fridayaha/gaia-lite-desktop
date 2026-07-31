"""DVP benchmark report generator (P5, DESIGN.md §九).

Consumes the results JSON emitted by the harness (--json flag) and renders a
Markdown report. Each case carries an "explanation" column — the METI
methodology (DESIGN.md §1.2) requires every case, especially anomalous ones
(XFAIL/XPASS/ERROR), to have a short rationale tying outcome to a known
defect or capability.

Usage:
    python -m tests.benchmark.dvp.scripts.generate_report \\
        --read results/read.json \\
        --out reports/<timestamp>.md

The report has:
  - Header (SUT, dataset, run timestamp, backend version).
  - Per-dimension summary (counts + correctness rate).
  - Per-case table with outcome / elapsed / Tax% / explanation.
  - Defect status table (D1-D4).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ── Case metadata: description + explanation template per outcome ──────────
# Source of truth for the "explanation" column. Each entry maps case_id →
# {desc, explain: {outcome → rationale}}. Outcomes not listed fall back to
# a generic note. This is what makes the report self-explaining (METI §1.2).

CASE_META: dict[str, dict[str, str | dict[str, str]]] = {
    "L1": {
        "desc": "单实体点查: project_code 反查项目信息",
        "explain": {"PASS": "VIRTUAL 点查走 textsql 编译路径，三段名 Trino 联邦直查"},
    },
    "L2": {
        "desc": "单实体过滤+排序: 项目下车型按 dev_tier",
        "explain": {
            "PASS": "D3 已根治：收编后 and/or 复合 filter 走编译器，不再 match-all",
        },
    },
    "L3": {
        "desc": "多表 JOIN 反查: change_point→6跳→项目令号",
        "explain": {"PASS": "多跳 traversal 通过多次单跳 API 调用 + 客户端 JOIN 完成"},
    },
    "L4": {
        "desc": "聚合统计: 项目下各工况 testItem 数量",
        "explain": {"PASS": "聚合走 execute_compiled_sql 编译路径（PR 5 收编）"},
    },
    "L5": {
        "desc": "LEFT JOIN: testItem LEFT JOIN spec",
        "explain": {"PASS": "null_allowed 断言容忍缺失关联，客户端 JOIN 兜底"},
    },
    "L6": {
        "desc": "增量查询: 项目最近变更的 component",
        "explain": {
            "PASS": "D3 已根治：range + and/or 复合 filter 编译路径正确",
        },
    },
    "L7": {
        "desc": "跨工况过滤: frontCollision 待执行 testItem",
        "explain": {
            "XPASS": "D1 后端缺陷仍在（4 工况 OT 共表 condition_type 未过滤）；"
                     "harness 客户端 conditionType 过滤绕过 → 通过。XPASS ≠ D1 修复",
            "XFAIL": "D1 缺陷：ObjectQueryService 不按 condition_type 过滤，查 FrontCollision 返回全部 200 行",
        },
    },
    "L8": {
        "desc": "range filter 数值: change_degree [3,5]",
        "explain": {
            "PASS": "D4 已根治：compiler 按 Literal.is_string 区分，数字字面量转 int/float 绑定原生类型",
        },
    },
    "L9": {
        "desc": "跨链路反查: dimension 被哪些 testItem 验证",
        "explain": {"PASS": "反向 traversal + 客户端 JOIN"},
    },
    "L10": {
        "desc": "多条件组合: 项目+工况+状态 testItem",
        "explain": {"PASS": "D3 已根治：三条件 and 复合 filter 编译路径正确"},
    },
    "L11": {
        "desc": "分页: testItem 按 create_time 分页",
        "explain": {"PASS": "ORDER BY + LIMIT/OFFSET 编译路径"},
    },
    "L12": {
        "desc": "VIRTUAL DATE range filter",
        "explain": {
            "XPASS": "D2 已根治：compiler 字面量类型保留 + harness DATE '...' 显式类型",
            "XFAIL": "D2 缺陷：DATE range 传字符串字面量，Trino date <= varchar TYPE_MISMATCH",
        },
    },
    "L13": {
        "desc": "共用表跨工况 UNION",
        "explain": {
            "XPASS": "D1 后端缺陷仍在；harness 客户端 conditionType 过滤绕过 → 通过。XPASS ≠ D1 修复",
            "XFAIL": "D1 缺陷：4 工况 OT 共表，condition_type 未过滤导致串数据",
        },
    },
    "L14": {
        "desc": "时间旅行: spec 快照",
        "explain": {
            "XFAIL": "设计预期：VIRTUAL 无 Iceberg snapshot，时间旅行不适用（仅 MANAGED 支持）",
        },
    },
    # ── Agent dimension (A1-A9) ──
    "A1": {
        "desc": "单实体点查 NL",
        "explain": {"PASS": "LLM 双模式均生成正确 SQL（prompt 给了 OT 属性后单实体点查可达成）"},
    },
    "A2": {
        "desc": "过滤+排序 NL",
        "explain": {"PASS": "单表过滤+排序双模式 2/3 通过（超阈值）"},
    },
    "A3": {
        "desc": "多表反查 NL（6 跳）",
        "explain": {"FAIL": "多跳 JOIN 是 Agent 瓶颈：LLM 生成的 logical/物理 SQL 多表关联易错（列名/JOIN 条件）"},
    },
    "A4": {
        "desc": "聚合 NL",
        "explain": {"FAIL": "LLM 难正确生成 GROUP BY + 多表关联的聚合 SQL"},
    },
    "A5": {
        "desc": "可选关联 NL（LEFT JOIN）",
        "explain": {"FAIL": "LEFT JOIN 语义 LLM 表达不稳定"},
    },
    "A6": {
        "desc": "增量 NL（range+过滤）",
        "explain": {"FAIL": "日期 range + 多表过滤组合，LLM 生成的 SQL 列名/JOIN 出错"},
    },
    "A7": {
        "desc": "跨工况 NL",
        "explain": {"FAIL": "跨工况需 JOIN condition_detail，LLM 多跳 JOIN 瓶颈"},
    },
    "A8": {
        "desc": "模糊/歧义 NL",
        "explain": {
            "XFAIL": "设计预期：模糊查询无确定答案，LLM 歧义处理是开放问题",
        },
    },
    "A9": {
        "desc": "多轮对话 NL",
        "explain": {
            "XFAIL": "设计预期：多轮对话需会话状态，当前单轮 API 不支持",
        },
    },
}

# Defect status table — rendered at the bottom of the report.
DEFECTS: list[dict[str, str]] = [
    {
        "id": "D1",
        "defect": "共用物理表多 OT 串数据（4 工况 OT 共表 condition_type 未过滤）",
        "status": "🔴 仍存在",
        "cases": "L7 / L13",
        "note": "harness 客户端 conditionType 过滤绕过 → XPASS。后端修复需 OT 级 discriminator 谓词支持",
    },
    {
        "id": "D2",
        "defect": "VIRTUAL DATE range filter 类型不匹配",
        "status": "✅ 根治",
        "cases": "L12",
        "note": "PR 4 收编 + compiler 字面量类型保留 + harness DATE '...' 显式类型",
    },
    {
        "id": "D3",
        "defect": "VIRTUAL and/or 复合 filter 失效（match-all）",
        "status": "✅ 根治",
        "cases": "L2/L3/L6/L10",
        "note": "PR 4 收编：/objects/load 手写旁路 + _filter_dict_to_sql 整体删除",
    },
    {
        "id": "D4",
        "defect": "compiler 字面量参数化丢类型（integer <= varchar）",
        "status": "✅ 根治",
        "cases": "L8",
        "note": "compiler 按 Literal.is_string 区分，数字字面量转 int/float 绑定原生类型",
    },
]


@dataclass
class DimResult:
    dimension: str
    total: int
    pass_n: int
    fail_n: int
    xfail_n: int
    xpass_n: int
    error_n: int
    skip_n: int
    counted: int
    correctness_rate: float
    results: list[dict]

    @classmethod
    def from_json(cls, data: dict) -> DimResult:
        return cls(
            dimension=data["dimension"],
            total=data["total"],
            pass_n=data["pass"],
            fail_n=data["fail"],
            xfail_n=data["xfail"],
            xpass_n=data["xpass"],
            error_n=data["error"],
            skip_n=data["skip"],
            counted=data["counted"],
            correctness_rate=data["correctness_rate"],
            results=data["results"],
        )


def _display_outcome(r: dict) -> str:
    """Display outcome: tier-2/3 PASS shows as XPASS (regression/workaround met)."""
    if r.get("is_regression"):
        return "XPASS"
    return r["outcome"]


def _explain(case_id: str, outcome: str) -> str:
    meta = CASE_META.get(case_id, {})
    explain = meta.get("explain", {})  # type: ignore[union-attr]
    text = explain.get(outcome)  # type: ignore[union-attr]
    if text:
        return text
    desc = meta.get("desc", "")
    return f"{desc}（{outcome}）"


def _outcome_emoji(outcome: str) -> str:
    return {
        "PASS": "✅",
        "FAIL": "❌",
        "XFAIL": "⚠️",
        "XPASS": "🟡",
        "ERROR": "💥",
        "ERROR_TIMEOUT": "⏱️",
        "skipped": "⏭️",
    }.get(outcome, "?")


def render(dim: DimResult, run_ts: str, backend_version: str) -> str:
    lines: list[str] = []
    lines.append("# DVP Benchmark Report")
    lines.append("")
    lines.append(f"- **Run**: {run_ts}")
    lines.append("- **SUT**: Gaia ontology backend (all-VIRTUAL, MySQL→Trino federation)")
    lines.append(f"- **Backend version**: {backend_version}")
    lines.append("- **Dataset**: `dvp_benchmark` (80,010 rows, seed=42, 21 MySQL tables)")
    lines.append("- **Ontology**: `DVP` (24 OT, 234 properties, 34 links, 0 actions, all VIRTUAL)")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Dimension | Total | PASS | FAIL | XFAIL | XPASS | ERROR | Correctness |")
    lines.append("|---|---|---|---|---|---|---|---|")
    rate = f"{dim.correctness_rate:.1%}" if dim.counted else "N/A"
    lines.append(
        f"| {dim.dimension} | {dim.total} | {dim.pass_n} | {dim.fail_n} | "
        f"{dim.xfail_n} | {dim.xpass_n} | {dim.error_n} | {dim.pass_n}/{dim.counted} = {rate} |"
    )
    lines.append("")
    lines.append(
        "> Correctness = PASS / (PASS+FAIL). XFAIL = expected fail (tier 2/3). "
        "XPASS = XFAIL unexpectedly passed (regression met, or harness-side workaround)."
    )
    lines.append("")

    # Per-case table
    lines.append(f"## {dim.dimension.capitalize()} dimension — per case")
    lines.append("")
    lines.append("| Case | Tier | Kind | Outcome | Elapsed | Tax% | Explanation |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in dim.results:
        tax = r.get("metrics", {}).get("tax_pct")
        tax_s = f"{tax:.0f}%" if tax is not None else "—"
        disp = _display_outcome(r)
        expl = _explain(r["case_id"], disp)
        # Cap explanation length for table readability.
        if len(expl) > 90:
            expl = expl[:87] + "…"
        lines.append(
            f"| {r['case_id']} | T{r['tier']} | {r['kind']} | "
            f"{_outcome_emoji(disp)} {disp} | "
            f"{r['elapsed_s']:.2f}s | {tax_s} | {expl} |"
        )
    lines.append("")

    # Detail section for anomalous cases (METI: every anomaly gets a paragraph).
    # Anomaly = anything not a clean tier-1 PASS (XPASS/XFAIL/FAIL/ERROR).
    anomalies = [
        r for r in dim.results
        if _display_outcome(r) != "PASS" or r["tier"] in (2, 3)
    ]
    if anomalies:
        lines.append("## Anomaly explanations")
        lines.append("")
        for r in anomalies:
            disp = _display_outcome(r)
            desc = CASE_META.get(r["case_id"], {}).get("desc", "")
            lines.append(f"### {r['case_id']} — {_outcome_emoji(disp)} {disp}")
            lines.append(f"- **Description**: {desc}")
            lines.append(f"- **Detail**: {r.get('detail', '') or '—'}")
            if r.get("expected_preview"):
                lines.append(f"- **Expected preview**: `{r['expected_preview'][:80]}`")
            if r.get("actual_preview"):
                lines.append(f"- **Actual preview**: `{r['actual_preview'][:80]}`")
            lines.append(f"- **Explanation**: {_explain(r['case_id'], disp)}")
            lines.append("")

    # Defect table
    lines.append("## Defect status")
    lines.append("")
    lines.append("| # | Defect | Status | Cases | Note |")
    lines.append("|---|---|---|---|---|")
    for d in DEFECTS:
        lines.append(f"| {d['id']} | {d['defect']} | {d['status']} | {d['cases']} | {d['note']} |")
    lines.append("")

    # McNemar for agent dimension (if present).
    mcnemar_p = None
    for r in dim.results:
        if r.get("metrics", {}).get("mcnemar_p") is not None:
            mcnemar_p = r["metrics"].get("mcnemar_p")
            b = r["metrics"].get("mcnemar_b")
            a = r["metrics"].get("mcnemar_a")
            break
    if mcnemar_p is not None:
        lines.append("## McNemar paired test (Text-to-Ontology vs Text-to-SQL)")
        lines.append("")
        lines.append(f"- b (onto-only pass) = {b}, a (sql-only pass) = {a}")
        lines.append(f"- p-value (two-sided exact) = {mcnemar_p:.4f}")
        lines.append(
            "> p > 0.05 → 无显著差异（双模式 LLM 错误模式一致）；p ≤ 0.05 → 某模式显著优于另一。"
        )
        lines.append("")

    # Performance summary (if metrics present)
    perf_cases = [r for r in dim.results if r.get("metrics", {}).get("tax_pct") is not None]
    if perf_cases:
        lines.append("## Performance (Tax% = ontology_p95 / raw_p95 − 1)")
        lines.append("")
        lines.append("| Case | onto_p95 | raw_p95 | Tax% |")
        lines.append("|---|---|---|---|")
        for r in perf_cases:
            m = r["metrics"]
            lines.append(
                f"| {r['case_id']} | {m.get('onto_p95_s', 0):.3f}s | "
                f"{m.get('raw_p95_s', 0):.3f}s | {m['tax_pct']:.0f}% |"
            )
        lines.append("")
        lines.append(
            "> Tax% = query overhead the ontology layer adds over raw MySQL. "
            "L8 (pure point+range) is the floor; multi-hop traversal (L4/L6) "
            "amplifies it via repeated Trino round-trips."
        )
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by `scripts/generate_report.py` at {run_ts}*")
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="DVP benchmark report generator")
    ap.add_argument("--read", metavar="PATH", help="read dimension results JSON")
    ap.add_argument("--agent", metavar="PATH", help="agent dimension results JSON (optional)")
    ap.add_argument("--out", metavar="PATH", required=True, help="output markdown path")
    ap.add_argument(
        "--backend-version", default="unknown", help="backend version string for the header"
    )
    args = ap.parse_args()

    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections: list[str] = []

    if args.read:
        data = json.loads(Path(args.read).read_text())
        dim = DimResult.from_json(data)
        sections.append(render(dim, run_ts, args.backend_version))

    if args.agent:
        data = json.loads(Path(args.agent).read_text())
        dim = DimResult.from_json(data)
        sections.append(render(dim, run_ts, args.backend_version))

    if not sections:
        print("error: provide at least --read or --agent", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(sections))
    print(f"report → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
