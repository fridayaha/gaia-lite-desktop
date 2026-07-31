"""Base harness primitives: result classification, timeouts, paired runs, stats.

DESIGN.md §八-1 (result classification) + §八-2 (robustness) + §七 (statistics).

A "case" is the atomic unit. Each case has an id, tier (1/2/3), kind, and a
pair of (expected, actual) producers. The harness runs them under a 60s
timeout (DESIGN.md §2.1), classifies the outcome, and emits a CaseResult
that feeds the report generator.

Principle: this module knows NOTHING about ontology/MySQL — it is a pure
harness kernel so the read/write/security/agent harnesses all reuse it.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

T = TypeVar("T")

# DESIGN.md §2.1 — hard per-case budget.
CASE_TIMEOUT_S = 60.0


class Outcome(StrEnum):
    """DESIGN.md §八-1 result taxonomy."""

    PASS = "PASS"
    FAIL = "FAIL"
    XFAIL = "XFAIL"  # expected fail (Tier2/3) — not counted in correctness
    ERROR = "ERROR"  # system crash / sync failure
    ERROR_TIMEOUT = "ERROR_TIMEOUT"
    ERROR_SYNC_TIMEOUT = "ERROR_SYNC_TIMEOUT"
    SKIPPED = "skipped"  # precondition unmet (e.g. table not synced)


# Tier → expected outcome when the case "fails" (Tier2/3 are XFAIL, not FAIL).
_TIER_EXPECTED_FAIL = {2, 3}


@dataclass
class CaseResult:
    case_id: str
    tier: int
    kind: str  # assertion kind (set_eq / count_eq / ...)
    outcome: Outcome
    detail: str = ""  # human-readable explanation (METI loop fuel)
    elapsed_s: float = 0.0
    expected_preview: str = ""
    actual_preview: str = ""
    # Performance / paired extras (filled by perf harness).
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def counted_for_correctness(self) -> bool:
        """Whether this result feeds the correctness rate (DESIGN.md §八-1)."""
        return self.outcome in (Outcome.PASS, Outcome.FAIL)

    @property
    def is_regression(self) -> bool:
        """An XFAIL case that unexpectedly PASSES = regression target met
        (DESIGN.md: '修复后转 PASS 触发告警'). Tracked, not failed."""
        return self.outcome == Outcome.PASS and self.tier in _TIER_EXPECTED_FAIL


def classify(case_id: str, tier: int, kind: str, passed: bool, detail: str = "") -> CaseResult:
    """Classify a finished assertion.

    Tier2/3 cases that FAIL are recorded as XFAIL (not FAIL) so they don't
    pollute the correctness rate — they are倒逼 targets, not regressions.
    A Tier2/3 case that PASSES is a regression-met signal (logged).
    """
    if passed:
        outcome = Outcome.PASS
    else:
        outcome = Outcome.XFAIL if tier in _TIER_EXPECTED_FAIL else Outcome.FAIL
    return CaseResult(case_id=case_id, tier=tier, kind=kind, outcome=outcome, detail=detail)


async def run_with_timeout(
    case_id: str,
    tier: int,
    kind: str,
    fn: Callable[[], Awaitable[CaseResult]],
    timeout_s: float = CASE_TIMEOUT_S,
) -> CaseResult:
    """Run a case coroutine under a hard timeout, classifying crashes/timeouts.

    DESIGN.md §2.1: timeout → ERROR_TIMEOUT (not FAIL, does not pollute the
    correctness rate). Unexpected exceptions → ERROR.
    """
    t0 = time.time()
    try:
        result = await asyncio.wait_for(fn(), timeout=timeout_s)
        result.elapsed_s = round(time.time() - t0, 3)
        return result
    except TimeoutError:
        return CaseResult(
            case_id=case_id,
            tier=tier,
            kind=kind,
            outcome=Outcome.ERROR_TIMEOUT,
            detail=f"exceeded {timeout_s}s budget",
            elapsed_s=round(time.time() - t0, 3),
        )
    except Exception as e:
        tb = traceback.format_exc()
        return CaseResult(
            case_id=case_id,
            tier=tier,
            kind=kind,
            outcome=Outcome.ERROR,
            detail=f"{type(e).__name__}: {e}\n{tb[-400:]}",
            elapsed_s=round(time.time() - t0, 3),
        )


@dataclass
class DimSummary:
    """Aggregate stats for one dimension (read/write/security/agent)."""

    dimension: str
    total: int = 0
    pass_n: int = 0
    fail_n: int = 0
    xfail_n: int = 0  # expected-fail that indeed failed
    xpass_n: int = 0  # xfail that unexpectedly passed (regression met)
    error_n: int = 0
    skip_n: int = 0
    results: list[CaseResult] = field(default_factory=list)

    def add(self, r: CaseResult) -> None:
        self.total += 1
        self.results.append(r)
        if r.outcome == Outcome.PASS:
            if r.tier in _TIER_EXPECTED_FAIL:
                self.xpass_n += 1
            else:
                self.pass_n += 1
        elif r.outcome == Outcome.FAIL:
            self.fail_n += 1
        elif r.outcome == Outcome.XFAIL:
            self.xfail_n += 1
        elif r.outcome == Outcome.SKIPPED:
            self.skip_n += 1
        else:  # ERROR_*
            self.error_n += 1

    @property
    def correctness_rate(self) -> float:
        """PASS / (PASS + FAIL) — ERROR/XFAIL/SKIP excluded (DESIGN.md §八-1)."""
        denom = self.pass_n + self.fail_n
        return self.pass_n / denom if denom else 0.0

    @property
    def counted(self) -> int:
        return self.pass_n + self.fail_n
