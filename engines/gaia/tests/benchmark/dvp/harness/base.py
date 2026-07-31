"""DVP benchmark — base harness primitives.

DESIGN.md §八-1 (result classification) + §八-2 (robustness) + §七 (stats).
A "case" is the atomic unit: id, tier (1/2/3), kind, (expected, actual)
producers. Runs under a 60s timeout (DESIGN.md §2.1), classifies outcome,
emits CaseResult feeding the report generator.

Pure harness kernel — knows nothing about ontology/MySQL. Reused by
read/agent harnesses. Independent of marketing (no shared code).

Diff vs marketing base: no ERROR_SYNC_TIMEOUT (DVP has no sync chain).
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# DESIGN.md §2.1 — hard per-case budget.
CASE_TIMEOUT_S = 60.0


class Outcome(StrEnum):
    """DESIGN.md §八-1 result taxonomy (DVP subset, no SYNC_TIMEOUT)."""

    PASS = "PASS"
    FAIL = "FAIL"
    XFAIL = "XFAIL"  # expected fail (Tier2/3) — not counted in correctness
    ERROR = "ERROR"  # system crash
    ERROR_TIMEOUT = "ERROR_TIMEOUT"
    SKIPPED = "skipped"  # precondition unmet (e.g. LLM not configured)


# Tier → expected outcome when the case "fails" (Tier2/3 are XFAIL, not FAIL).
_TIER_EXPECTED_FAIL = {2, 3}


@dataclass
class CaseResult:
    case_id: str
    tier: int
    kind: str  # assertion kind (set_eq / count_eq / ...)
    outcome: Outcome
    detail: str = ""
    elapsed_s: float = 0.0
    expected_preview: str = ""
    actual_preview: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def counted_for_correctness(self) -> bool:
        return self.outcome in (Outcome.PASS, Outcome.FAIL)

    @property
    def is_regression(self) -> bool:
        """XFAIL case that unexpectedly PASSES = regression target met."""
        return self.outcome == Outcome.PASS and self.tier in _TIER_EXPECTED_FAIL


def classify(case_id: str, tier: int, kind: str, passed: bool, detail: str = "") -> CaseResult:
    """Classify a finished assertion. Tier2/3 FAIL → XFAIL (not FAIL)."""
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
    """Run a case under a hard timeout. Timeout → ERROR_TIMEOUT (not FAIL)."""
    t0 = time.time()
    try:
        result = await asyncio.wait_for(fn(), timeout=timeout_s)
        result.elapsed_s = round(time.time() - t0, 3)
        return result
    except TimeoutError:
        return CaseResult(
            case_id=case_id, tier=tier, kind=kind, outcome=Outcome.ERROR_TIMEOUT,
            detail=f"exceeded {timeout_s}s budget", elapsed_s=round(time.time() - t0, 3),
        )
    except Exception as e:
        tb = traceback.format_exc()
        return CaseResult(
            case_id=case_id, tier=tier, kind=kind, outcome=Outcome.ERROR,
            detail=f"{type(e).__name__}: {e}\n{tb[-400:]}",
            elapsed_s=round(time.time() - t0, 3),
        )


@dataclass
class DimSummary:
    """Aggregate stats for one dimension (read/agent)."""

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
        """PASS / (PASS + FAIL) — ERROR/XFAIL/SKIP excluded."""
        denom = self.pass_n + self.fail_n
        return self.pass_n / denom if denom else 0.0

    @property
    def counted(self) -> int:
        return self.pass_n + self.fail_n
