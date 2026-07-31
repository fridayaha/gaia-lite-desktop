"""DVP assertion engine: multi-kind comparison + partial credit (DESIGN.md §六).

Supports the kinds DVP uses (read path only — no action_rejected/forbidden,
DVP has no write dimension). Each kind takes (expected, actual) and returns
an AssertionVerdict (passed + detail + partial-credit score).

Design notes:
  - set_eq / ordered_list dedup (JOIN may produce duplicate rows — set semantics).
  - camelCase↔snake_case bidirectional matching (API camelCase vs physical snake_case).
  - partial credit: ordered_list falls back to Jaccard (DESIGN.md §六, after Spider).
  - Anti-enumeration: count_eq / all_null forbid "list everything" answers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# ── camelCase ↔ snake_case normalization ─────────────────────────────────────
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(s: str) -> str:
    return _CAMEL_RE.sub("_", s).lower()


def _normalize_key(k: str) -> str:
    """Normalize a dict key: camelCase → snake_case lower. Lets us compare
    API results (camelCase apiNames) against physical SQL (snake_case columns)."""
    return _to_snake(str(k))


def _normalize_row(row: dict[str, Any], select_keys: Sequence[str] | None = None) -> frozenset[tuple[str, Any]]:
    """Normalize a row into a hashable frozenset of (norm_key, value)."""
    wanted = {_normalize_key(k) for k in select_keys} if select_keys else None
    out = []
    for k, v in row.items():
        nk = _normalize_key(k)
        if wanted is not None and nk not in wanted:
            continue
        out.append((nk, _normalize_value(v)))
    return frozenset(out)


def _normalize_value(v: Any) -> Any:
    """Coerce values for stable comparison across DB drivers.
    datetime → naive ISO string; Decimal → float; None stays None."""
    import datetime
    from decimal import Decimal

    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.replace(tzinfo=None).isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


@dataclass
class AssertionVerdict:
    passed: bool
    detail: str = ""
    score: float = 1.0  # partial credit in [0,1]


# ── Kind implementations ─────────────────────────────────────────────────────


def set_eq(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    select_keys: Sequence[str] | None = None,
) -> AssertionVerdict:
    """Unordered set equality (dedup). DESIGN.md §六 set_eq."""
    exp_set = {_normalize_row(r, select_keys) for r in expected}
    act_set = {_normalize_row(r, select_keys) for r in actual}
    if exp_set == act_set:
        return AssertionVerdict(True, f"set_eq: {len(exp_set)} rows match")
    missing = exp_set - act_set
    extra = act_set - exp_set
    detail = f"set_eq mismatch: {len(missing)} missing, {len(extra)} extra (exp={len(exp_set)} act={len(act_set)})"
    if missing:
        detail += f"; missing_sample={_preview(list(missing)[0])}"
    if extra:
        detail += f"; extra_sample={_preview(list(extra)[0])}"
    return AssertionVerdict(False, detail)


def ordered_list(
    expected: Sequence[dict[str, Any]],
    actual: Sequence[dict[str, Any]],
    select_keys: Sequence[str] | None = None,
    jaccard_threshold: float = 0.9,
) -> AssertionVerdict:
    """Ordered list equality with Jaccard fallback (DESIGN.md §六, L2/L11).

    Exact order match → PASS. Otherwise Jaccard on row sets; PASS if ≥ threshold
    (regression #1: order_by may be partially correct). Partial credit = jaccard.
    """
    exp_rows = [_normalize_row(r, select_keys) for r in expected]
    act_rows = [_normalize_row(r, select_keys) for r in actual]
    if exp_rows == act_rows:
        return AssertionVerdict(True, f"ordered_list: exact match ({len(exp_rows)} rows)")
    exp_set, act_set = set(exp_rows), set(act_rows)
    union = exp_set | act_set
    jacc = len(exp_set & act_set) / len(union) if union else 1.0
    passed = jacc >= jaccard_threshold
    detail = (
        f"ordered_list: order/rows differ; jaccard={jacc:.3f} "
        f"(threshold={jaccard_threshold}); exp={len(exp_set)} act={len(act_set)}"
    )
    return AssertionVerdict(passed, detail, score=jacc)


def count_eq(expected: Iterable[dict[str, Any]], actual: Iterable[dict[str, Any]]) -> AssertionVerdict:
    """Row-count equality (DESIGN.md §六 count_eq). Anti-enumeration."""
    exp_n = sum(1 for _ in expected)
    act_n = sum(1 for _ in actual)
    passed = exp_n == act_n
    return AssertionVerdict(passed, f"count_eq: expected={exp_n} actual={act_n}")


def count_range(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    tolerance: int = 1,
) -> AssertionVerdict:
    """Row-count within ±tolerance (DESIGN.md §六 count_range)."""
    exp_n = sum(1 for _ in expected)
    act_n = sum(1 for _ in actual)
    passed = abs(exp_n - act_n) <= tolerance
    return AssertionVerdict(passed, f"count_range: expected={exp_n} actual={act_n} (tol=±{tolerance})")


def all_null(actual: Iterable[dict[str, Any]], select_keys: Sequence[str]) -> AssertionVerdict:
    """Every value in select_keys is null across all rows (DESIGN.md §六)."""
    wanted = {_normalize_key(k) for k in select_keys}
    rows = list(actual)
    if not rows:
        return AssertionVerdict(False, "all_null: no rows returned (expected ≥1 all-null row)")
    bad = []
    for i, row in enumerate(rows):
        row_vals = {k: v for k, v in _normalize_row(row, select_keys)}
        for k in wanted:
            if row_vals.get(k) is not None:
                bad.append((i, k, row_vals.get(k)))
    if not bad:
        return AssertionVerdict(True, f"all_null: {len(rows)} rows, all {select_keys} null")
    return AssertionVerdict(False, f"all_null: {len(bad)} non-null values; sample={bad[:3]}")


def null_allowed(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    select_keys: Sequence[str] | None = None,
    nullable_keys: Sequence[str] | None = None,
) -> AssertionVerdict:
    """set_eq but specific columns may be null (DESIGN.md §六, L5 LEFT JOIN)."""
    _ = {_normalize_key(k) for k in (nullable_keys or [])}  # recorded for future per-column tolerance
    return set_eq(expected, actual, select_keys)


def snapshot_diff(expected: Any, actual: Any) -> AssertionVerdict:
    """Snapshot diff (DESIGN.md §六, L14 time-travel). VIRTUAL has no snapshots —
    always reports the expected-XFAIL state; harness marks Tier3 XFAIL upstream."""
    return AssertionVerdict(False, "snapshot_diff: VIRTUAL has no snapshots (expected XFAIL)")


def _preview(row: Any, maxlen: int = 120) -> str:
    s = str(row)
    return s if len(s) <= maxlen else s[:maxlen] + "…"


# ── Dispatcher ───────────────────────────────────────────────────────────────


def assert_by_kind(kind: str, expected: Any, actual: Any, **opts) -> AssertionVerdict:
    """Dispatch a comparison by kind string. DESIGN.md §六 table (DVP subset)."""
    if kind == "set_eq":
        return set_eq(expected, actual, opts.get("select_keys"))
    if kind == "set_equiv":
        return set_eq(expected, actual, opts.get("select_keys"))
    if kind == "ordered_list":
        return ordered_list(expected, actual, opts.get("select_keys"), opts.get("jaccard_threshold", 0.9))
    if kind == "count_eq":
        return count_eq(expected, actual)
    if kind == "count_range":
        return count_range(expected, actual, opts.get("tolerance", 1))
    if kind == "all_null":
        return all_null(actual, opts["select_keys"])
    if kind == "null_allowed":
        return null_allowed(expected, actual, opts.get("select_keys"), opts.get("nullable_keys"))
    if kind == "snapshot_diff":
        return snapshot_diff(expected, actual)
    raise ValueError(f"unknown assertion kind: {kind}")
