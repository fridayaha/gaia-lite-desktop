"""Single-shot full benchmark orchestrator (DESIGN.md §八-4, §10 P7).

Runs all four dimensions under a global 2h wall-clock budget (DESIGN.md §2.1),
collects DimSummary objects, and hands them to generate_report. Single run —
no stitching of multiple runs (DESIGN.md §2.5).

Usage:
    .venv/bin/python -m tests.benchmark.marketing.scripts.run_benchmark [--skip-write] [--skip-agent]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("benchmark")

GLOBAL_BUDGET_S = 2 * 60 * 60  # 2h
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


async def _run_dim(name: str, coro_factory, summaries: dict, budget_used: list[float]) -> float:
    """Run one dimension, return elapsed seconds. Aborts on global budget exceed."""
    remaining = GLOBAL_BUDGET_S - budget_used[0]
    if remaining <= 0:
        log.warning("Global budget exhausted — skipping %s", name)
        summaries[name] = None
        return 0.0
    log.info("═══════ %s dimension (budget remaining %.0fs) ═══════", name, remaining)
    t0 = time.perf_counter()
    try:
        summary = await asyncio.wait_for(coro_factory(), timeout=remaining)
        summaries[name] = summary
    except TimeoutError:
        log.error("%s exceeded its budget — partial results only", name)
        summaries[name] = None
    except Exception as e:
        log.exception("%s dimension crashed: %s", name, e)
        summaries[name] = None
    elapsed = time.perf_counter() - t0
    budget_used[0] += elapsed
    log.info("%s done in %.1fs (total used %.1fs)", name, elapsed, budget_used[0])
    return elapsed


async def main_async(args) -> dict:
    from tests.benchmark.marketing.harness import agent_harness, read_harness, security_harness, write_harness

    summaries: dict = {}
    budget_used = [0.0]
    timings: dict = {}

    timings["read"] = await _run_dim("read", read_harness.run_all, summaries, budget_used)
    if not args.skip_write:
        timings["write"] = await _run_dim("write", write_harness.run_all, summaries, budget_used)
    timings["security"] = await _run_dim("security", security_harness.run_all, summaries, budget_used)
    if not args.skip_agent:
        timings["agent"] = await _run_dim("agent", agent_harness.run_all, summaries, budget_used)

    # Generate report.
    from tests.benchmark.marketing.scripts import generate_report

    REPORT_DIR.mkdir(exist_ok=True)
    report_path = generate_report.write_report(summaries, timings, REPORT_DIR)
    log.info("═══════ Report written to %s ═══════", report_path)
    return summaries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-write", action="store_true")
    ap.add_argument("--skip-agent", action="store_true")
    args = ap.parse_args()
    t0 = time.perf_counter()
    asyncio.run(main_async(args))
    log.info("Total wall-clock: %.1fs", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
