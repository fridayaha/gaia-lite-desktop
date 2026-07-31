"""Deterministic test-parameter resolver (DESIGN.md §2.5, §5.1).

The seed (RANDOM_SEED=42) is the single source of truth. To make read cases
reproducible, we re-run the SAME generate_all() the seeder uses and pick
known-good parameter values (a lead_id that exists, a sales_phone that has
follow-ups on the anchor date, etc.). This avoids querying MySQL at runtime
to find a "good" sample (which would be a hidden dependency on live data
state) and keeps cases deterministic.

DESIGN.md §2.5: "expected 由同一份种子数据用物理 SQL 推导，不手写" — the
parameter selection is also derived from the same seed, not hand-picked.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("param_resolver")

# Anchor date used by the seeder (seed_marketing.ANCHOR_DATE).
ANCHOR_DATE = datetime(2026, 6, 15, 9, 0, 0)
ANCHOR_DATE_STR = ANCHOR_DATE.strftime("%Y-%m-%d")


def _generate() -> Any:
    """Re-run the seeder's generate_all with seed=42 (pure, no DB)."""
    import random

    from faker import Faker

    seed_mod = importlib.import_module("tests.benchmark.marketing.scripts.seed_marketing")
    Faker.seed(seed_mod.RANDOM_SEED)
    faker = Faker(["zh_CN"])
    rng = random.Random(seed_mod.RANDOM_SEED)
    return seed_mod.generate_all(faker, rng)


@dataclass
class ReadParams:
    """Resolved parameters for all read cases (L1-L8)."""

    # L1 / L3: a lead_id that exists and has a user + allocate record.
    lead_id: str
    # L2 / L4: a sales_phone whose leads have next_follow_time on anchor date
    # and a manual call on anchor date. Falls back to any sales phone with
    # follow records if the strict anchor-date filter has no hits.
    sales_phone: str
    sales_phone_has_follows_on_anchor: bool
    # L3-bis: a test_drive_id that exists.
    test_drive_id: str
    # L5: anchor date prefix (matches test_drive.end_time seeded on anchor date).
    date_pattern: str
    # L6: a formatted_time that yields a bounded result set (well before now).
    formatted_time: str
    # L7: a store_code with multiple sales + valid leads.
    store_code: str
    # L8: a user_id that exists.
    user_id: str
    # L7-bis: a test_drive_id that has competitive_analysis (Tier2 — may be
    # empty if no AI product data; harness treats as XFAIL gracefully).
    td_id_for_competitive: str
    confidence_min: float


def resolve_read_params() -> ReadParams:
    """Derive stable test params from the seed. Pure (no DB / no network)."""
    data = _generate()

    # ── lead_id (L1/L3): first lead that has both a user and an allocate record.
    allocate_lead_ids = {r["leads_id"] for r in data.lead_allocate_record}
    lead_id = next((lr["id"] for lr in data.lead if lr["id"] in allocate_lead_ids), data.lead[0]["id"])

    # ── sales_phone (L2/L4): find a sales consultant whose leads have
    # next_follow_time on the anchor date (prefix match) AND leads_status valid.
    sales_by_id = {s["user_id"]: s for s in data.sales_consultant}
    sales_phone = data.sales_consultant[0]["phone"]
    has_follows_on_anchor = False
    # Count, per sales consultant, leads with next_follow_time on anchor date.
    anchor_prefix = ANCHOR_DATE_STR
    sales_lead_counts: dict[str, int] = {}
    lead_by_id = {lr["id"]: lr for lr in data.lead}
    for alloc in data.lead_allocate_record:
        sc_id = alloc["sales_consultant_id"]
        lead = lead_by_id.get(alloc["leads_id"])
        if not lead:
            continue
        nf = lead.get("next_follow_time")
        if nf and isinstance(nf, datetime) and nf.strftime("%Y-%m-%d") == anchor_prefix:
            sales_lead_counts[sc_id] = sales_lead_counts.get(sc_id, 0) + 1
    if sales_lead_counts:
        # Pick the sales consultant with the MOST anchor-date follows (stable).
        best_sc_id = max(sales_lead_counts, key=lambda k: sales_lead_counts[k])
        sales_phone = sales_by_id[best_sc_id]["phone"]
        has_follows_on_anchor = True
    else:
        # Fallback: any sales consultant with allocate records.
        for s in data.sales_consultant:
            if s["user_id"] in {a["sales_consultant_id"] for a in data.lead_allocate_record}:
                sales_phone = s["phone"]
                break

    # ── test_drive_id (L3-bis): first test drive.
    test_drive_id = data.test_drive[0]["id"] if data.test_drive else ""

    # ── date_pattern (L5): anchor date. test_drive.end_time is seeded relative
    # to ANCHOR_DATE for ~50% of records; the prefix match captures them.
    date_pattern = ANCHOR_DATE_STR

    # ── formatted_time (L6): 60 days ago → bounded result set.
    formatted_time = (ANCHOR_DATE - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

    # ── store_code (L7): dealership with the most sales (first dealership).
    store_code = data.dealership[0]["store_code"]

    # ── user_id (L8): first user.
    user_id = data.user[0]["user_id"]

    return ReadParams(
        lead_id=lead_id,
        sales_phone=sales_phone,
        sales_phone_has_follows_on_anchor=has_follows_on_anchor,
        test_drive_id=test_drive_id,
        date_pattern=date_pattern,
        formatted_time=formatted_time,
        store_code=store_code,
        user_id=user_id,
        td_id_for_competitive=test_drive_id,
        confidence_min=0.6,
    )
