"""Assertion engine: multi-kind comparison + partial credit (DESIGN.md §六).

Supports the kinds listed in DESIGN.md §六 table. Each kind takes
(expected, actual) and returns an AssertionVerdict (passed + detail).

Design notes:
  - set_eq / ordered_list dedup (DESIGN.md §12 关键陷阱: lead_allocate_record
    one-to-many JOIN produces duplicate rows — set semantics required).
  - camelCase↔snake_case bidirectional matching (DESIGN.md §六: pragmatic
    handling of physical vs API naming divergence).
  - partial credit decomposes a read query into SQL components (filter /
    order / limit / aggregate) for F1 scoring (DESIGN.md §六, after Spider).
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
    """Normalize a dict key for comparison: camelCase → snake_case lower.

    Lets us compare API results (camelCase apiNames) against physical SQL
    results (snake_case columns) without a brittle per-field mapping.
    """
    return _to_snake(str(k))


def _normalize_row(row: dict[str, Any], select_keys: Sequence[str] | None = None) -> frozenset[tuple[str, Any]]:
    """Normalize a result row into a hashable frozenset of (norm_key, value).

    If select_keys is given, only those (normalized) keys are kept — this is
    how set_eq scopes comparison to the columns the case cares about.
    """
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

    - datetimes → naive ISO string (strip tz; MySQL is naive, API may attach +00:00).
    - Decimal → float.
    - None stays None (null_allowed / all_null depend on it).
    """
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
    score: float = 1.0  # partial credit in [0,1] (DESIGN.md §六 partial credit)


# ── Kind implementations ─────────────────────────────────────────────────────


def set_eq(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    select_keys: Sequence[str] | None = None,
) -> AssertionVerdict:
    """Unordered set equality (dedup). DESIGN.md §六 set_eq.

    Rows are normalized to frozensets of (norm_key, value) so column-name
    case and ordering don't matter, and duplicate JOIN rows collapse.
    """
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
    """Ordered list equality with Jaccard fallback (DESIGN.md §六, L2).

    If order matches exactly → PASS. Otherwise compute Jaccard similarity on
    the row sets; PASS if ≥ threshold (regression #1: order_by may be
    partially correct). Reports both exact-match and jaccard in detail.
    """
    exp_rows = [_normalize_row(r, select_keys) for r in expected]
    act_rows = [_normalize_row(r, select_keys) for r in actual]
    exact = exp_rows == act_rows
    if exact:
        return AssertionVerdict(True, f"ordered_list: exact match ({len(exp_rows)} rows)")
    exp_set, act_set = set(exp_rows), set(act_rows)
    union = exp_set | act_set
    jacc = len(exp_set & act_set) / len(union) if union else 1.0
    passed = jacc >= jaccard_threshold
    # Partial credit = jaccard (DESIGN.md §六 partial credit).
    detail = (
        f"ordered_list: order/rows differ; jaccard={jacc:.3f} "
        f"(threshold={jaccard_threshold}); exp={len(exp_set)} act={len(act_set)}"
    )
    return AssertionVerdict(passed, detail, score=jacc)


def count_eq(expected: Iterable[dict[str, Any]], actual: Iterable[dict[str, Any]]) -> AssertionVerdict:
    """Row-count equality (DESIGN.md §六 count_eq). Anti-enumeration: forbids
    'list all' answers because the count must match exactly."""
    exp_n = sum(1 for _ in expected)
    act_n = sum(1 for _ in actual)
    passed = exp_n == act_n
    return AssertionVerdict(passed, f"count_eq: expected={exp_n} actual={act_n}")


def count_range(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    tolerance: int = 1,
) -> AssertionVerdict:
    """Row-count within ±tolerance (DESIGN.md §六 count_range, L7-bis)."""
    exp_n = sum(1 for _ in expected)
    act_n = sum(1 for _ in actual)
    passed = abs(exp_n - act_n) <= tolerance
    return AssertionVerdict(passed, f"count_range: expected={exp_n} actual={act_n} (tol=±{tolerance})")


def all_null(actual: Iterable[dict[str, Any]], select_keys: Sequence[str]) -> AssertionVerdict:
    """Every value in select_keys is null across all rows (DESIGN.md §六, L8)."""
    wanted = {_normalize_key(k) for k in select_keys}
    rows = list(actual)
    if not rows:
        return AssertionVerdict(False, "all_null: no rows returned (expected ≥1 all-null row)")
    bad = []
    for i, row in enumerate(rows):
        # _normalize_row filters to wanted keys; check each wanted key is None.
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
    """set_eq but specific columns may be null (DESIGN.md §六, L5 LEFT JOIN).

    Compares the non-nullable columns strictly; for nullable columns, only
    requires that wherever expected is non-null, actual matches (and null
    stays null when there's no join hit).
    """
    # nullable_keys recorded for future per-column tolerance; current impl
    # uses plain set_eq (None==None already holds under normalization).
    _ = {_normalize_key(k) for k in (nullable_keys or [])}
    return set_eq(expected, actual, select_keys)


def action_rejected(status_code: int, expected_reject: tuple[int, ...] = (403, 422)) -> AssertionVerdict:
    """Action/security case: request must be rejected (DESIGN.md §六)."""
    passed = status_code in expected_reject
    return AssertionVerdict(passed, f"action_rejected: status={status_code} (expected one of {expected_reject})")


def forbidden(state_changed: bool) -> AssertionVerdict:
    """Short-cut audit: forbidden state must NOT have changed (DESIGN.md §六)."""
    return AssertionVerdict(not state_changed, f"forbidden: state_changed={state_changed} (must be False)")


def _preview(row: Any, maxlen: int = 120) -> str:
    s = str(row)
    return s if len(s) <= maxlen else s[:maxlen] + "…"


# ── Dispatcher ───────────────────────────────────────────────────────────────


def assert_by_kind(kind: str, expected: Any, actual: Any, **opts) -> AssertionVerdict:
    """Dispatch a comparison by kind string. DESIGN.md §六 table."""
    if kind == "set_eq":
        return set_eq(expected, actual, opts.get("select_keys"))
    if kind == "set_equiv":
        return set_eq(expected, actual, opts.get("select_keys"))  # same semantics
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
    if kind == "action_rejected":
        return action_rejected(actual, opts.get("expected_reject", (403, 422)))
    if kind == "forbidden":
        return forbidden(actual)
    raise ValueError(f"unknown assertion kind: {kind}")
