"""DVP deterministic test-parameter resolver (DESIGN.md §2.5, §5.1).

The seed (RANDOM_SEED=42) is the single source of truth. We re-run the SAME
generate_all() the seeder uses and pick known-good parameter values (a
project_code that exists, a change_point_id with a real component chain,
etc.). This avoids querying MySQL at runtime to find "good" samples (hidden
dependency on live data state) and keeps cases deterministic.

DESIGN.md §2.5: "expected 由同一份种子数据用物理 SQL 推导，不手写" — the
parameter selection is also derived from the same seed, not hand-picked.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("param_resolver")


def _generate() -> Any:
    """Re-run the seeder's generate_all with seed=42 (pure, no DB)."""
    import random

    from faker import Faker

    seed_mod = importlib.import_module("tests.benchmark.dvp.scripts.seed_dvp")
    Faker.seed(seed_mod.RANDOM_SEED)
    faker = Faker(["zh_CN"])
    rng = random.Random(seed_mod.RANDOM_SEED)
    return seed_mod.generate_all(faker, rng)


@dataclass
class ReadParams:
    """Resolved parameters for all DVP read cases (L1-L14)."""

    # L1: a project_code that exists (first project_base row).
    project_code: str
    # L3: a change_point_id whose component → structure → body → vehicle →
    # project chain is intact (first change_point_entity row).
    change_point_id: str
    # L4: a project_code with a dvp_design + experiment_item_round chain
    # (first project_base, which always has a dvp_design).
    project_code_for_agg: str
    # L5: a detail_condition_code that has test_items (first oper_condition_detail).
    detail_condition_code: str
    # L6: a project_code + since_time for incremental component query.
    project_code_for_incremental: str
    since_time: str
    # L9: a dimension_id that is verified by ≥1 test_item (first lms_target_dimension).
    dimension_id: str
    # L10: a project_code + condition_type + status for composite filter.
    condition_type: str
    status: str
    # L11: a condition_type for pagination.
    condition_type_for_page: str
    # L12: a date range [start_date, end_date] for iteration query.
    start_date: str
    end_date: str
    # L14: a spec_code (Tier3 time-travel — XFAIL, no snapshot).
    spec_code: str


def resolve_read_params() -> ReadParams:
    """Derive stable test params from the seed. Pure (no DB / no network)."""
    data = _generate()

    # ── project_code (L1/L4/L6/L10): first project_base.
    project_code = data.project_base[0]["project_code"]

    # ── change_point_id (L3): first change_point_entity. Its component_id FK
    # points at a real component, which has structure_code → structure → body
    # → vehicle → project (seed guarantees topological integrity).
    change_point_id = data.change_point_entity[0]["change_point_id"]

    # ── detail_condition_code (L5): first oper_condition_detail (front_collision).
    detail_condition_code = data.oper_condition_detail[0]["detail_condition_code"]

    # ── since_time (L6): 2026-01-01 — captures most components (seeded in 2026).
    since_time = "2026-01-01 00:00:00"

    # ── dimension_id (L9): first lms_target_dimension. test_items are seeded
    # with random dimension_id, so the first dimension likely has ≥1 verifier.
    dimension_id = data.lms_target_dimension[0]["dimension_id"]

    # ── condition_type (L7/L10/L11): front_collision (always present, 50 rows).
    condition_type = "front_collision"
    # status for L10: "1" (待执行) — seeded across test_items.
    status = "1"

    # ── L12 date range: full 2026 H1.
    start_date = "2026-01-01"
    end_date = "2026-06-30"

    # ── spec_code (L14): first spec.
    spec_code = data.spec[0]["spec_code"]

    return ReadParams(
        project_code=project_code,
        change_point_id=change_point_id,
        project_code_for_agg=project_code,
        detail_condition_code=detail_condition_code,
        project_code_for_incremental=project_code,
        since_time=since_time,
        dimension_id=dimension_id,
        condition_type=condition_type,
        status=status,
        condition_type_for_page=condition_type,
        start_date=start_date,
        end_date=end_date,
        spec_code=spec_code,
    )
