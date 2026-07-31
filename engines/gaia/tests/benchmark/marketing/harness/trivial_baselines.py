"""Trivial baselines (DESIGN.md §3 Task Validity, §4 trivial attacker/agent).

A trivial baseline is a deliberately-dumb "solution" that should NOT pass a
real case. If it does, the case is too weak (e.g. accepts enumeration). The
harness runs each case against these baselines and reports their pass-rate;
high trivial pass-rate = case invalidity signal (DESIGN.md §6 诚实报告).

Read-path baselines:
  - do_nothing: return empty set.
  - dump_all:   return ALL rows of the queried OT (no filter).
  - random_id:  return a row for a random object id (point-query cases).
  - enumeration: return a list of all possible ids (must be rejected by
    anti-enumeration kinds like count_eq / all_null).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any


def do_nothing() -> list[dict[str, Any]]:
    """Return empty — the laziest possible answer."""
    return []


def dump_all(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every row unfiltered — should fail any scoped filter case."""
    return list(rows)


def random_id(rows: list[dict[str, Any]], id_key: str, seed: int = 42) -> list[dict[str, Any]]:
    """Return a single row for a random id — should fail multi-row cases."""
    if not rows:
        return []
    rng = random.Random(seed)
    return [rng.choice(rows)]


def enumeration(rows: Iterable[dict[str, Any]], id_key: str) -> list[dict[str, Any]]:
    """Return the full id universe — anti-enumeration kinds must reject this
    (they require exact count or uniqueness, not 'list everything')."""
    seen = set()
    out = []
    for r in rows:
        v = r.get(id_key)
        if v is not None and v not in seen:
            seen.add(v)
            out.append({id_key: v})
    return out


# Mapping case→applicable baselines (DESIGN.md §4.1 trivial baseline column).
READ_BASELINES = {
    "L1": ["do_nothing", "dump_all", "random_id"],
    "L2": ["do_nothing", "dump_all"],
    "L3": ["do_nothing", "dump_all"],
    "L4": ["do_nothing", "dump_all"],  # count_eq: dump_all would give wrong count
    "L5": ["do_nothing", "dump_all"],
    "L6": ["do_nothing", "dump_all"],
    "L7": ["do_nothing", "dump_all", "enumeration"],
    "L8": ["do_nothing"],  # all_null: do_nothing fails (expects ≥1 null row)
}
