"""Marketing benchmark — fixture seeding (DESIGN.md §5.1, exception 1 of principle 3.5).

Generates the source-side MySQL physical data for the Marketing benchmark,
with a FIXED seed (RANDOM_SEED=42) and Faker zh_CN locale (DESIGN.md §2.4).
This is **fixture seeding** (system input), NOT a write through the system API —
it is the explicit exception to the "no direct DB writes" rule.

What this script does (in order):
  1. Connect to the source MySQL (env-configurable, defaults for local docker).
  2. CREATE the marketing_benchmark schema + all physical tables (DDL).
  3. Generate deterministic data per DESIGN.md §2.3 volume table:
       - 主数据: dealership / sales_consultant / lead_source / user
       - 线索链路: lead / lead_allocate_record / lead_distribute_record / lead_follow_record
       - 外呼: manual_outbound_call / ai_outbound_call
       - 试驾: test_drive / test_drive_car / test_drive_route
       - 微信: chat_record
       - recording (SYNTHETIC, fix 3): merged from 3 url sources, recording_id = sha1(src)[:16]
  4. INSERT all rows (batched).

What this script does NOT do (must go through system API):
  - Writing into Iceberg / Doris (that is the system sync pipeline's job).
  - AI products (试驾报告 5 表 / 用户画像 8 表) — those are ontology-created
    via Action W7/W8, generated at benchmark run time, not seeded.

Determinism:
  - Faker.seed(42) + random.seed(42) → all values reproducible.
  - recording_id = sha1(f"{source_table}:{original_record_url}").hexdigest()[:16]
    (deterministic from the url, fix 3).

Usage:
    python -m tests.benchmark.marketing.scripts.seed_marketing \
        [--host localhost] [--port 3306] [--user root] [--password ""] \
        [--drop]  # drop & recreate schema before seeding (idempotent re-runs)

Connection params default to a local MySQL; override via env MARKETING_MYSQL_*.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import random
import string
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import aiomysql

try:
    from faker import Faker
except ImportError as e:  # pragma: no cover
    sys.exit(f"Faker is required for seeding: {e}. Install with `uv sync --extra dev`.")

logger = logging.getLogger("seed_marketing")

# ── Determinism ───────────────────────────────────────────────────────────
RANDOM_SEED = 42
SCHEMA = "marketing_benchmark"

# ── Volumes (DESIGN.md §2.3) ──────────────────────────────────────────────
VOLUMES = {
    "dealership": 20,
    "sales_consultant": 200,  # 10 per dealership
    "lead_source": 30,
    "user": 10_000,
    "lead": 10_000,  # 1:1 with user
    "lead_allocate_record": 30_000,  # ~3 ops per lead
    "lead_distribute_record": 15_000,
    "lead_follow_record": 30_000,  # ~3 follows per lead
    "manual_outbound_call": 20_000,
    "ai_outbound_call": 10_000,
    "test_drive": 5_000,  # ~50% of leads
    "test_drive_car": 200,  # 10 per dealership
    "test_drive_route": 100,  # 5 per dealership
    "chat_record": 20_000,
}

# Reference time: a fixed "today" so date-prefixed queries (LIKE 'YYYY-MM-DD%')
# have deterministic hits. Benchmark runs assume data is relative to this anchor.
ANCHOR_DATE = datetime(2026, 6, 15, 9, 0, 0)  # a Monday 09:00

# Business dictionary (DESIGN.md §2.4: display values Chinese, stored codes ASCII)
LEADS_STATUS_VALID = "100410"  # 有效线索
ORDER_STATUS_PENDING = ("1", "2", "3")  # 待执行（排程中）
ORDER_STATUS_DONE = ("4", "5", "6")  # 已完成
OP_TYPE = {"1": "下发", "2": "分配", "3": "转移", "4": "回收"}


@dataclass
class GeneratedData:
    """Container for all generated rows, kept in insertion order."""

    dealership: list[dict] = field(default_factory=list)
    sales_consultant: list[dict] = field(default_factory=list)
    lead_source: list[dict] = field(default_factory=list)
    user: list[dict] = field(default_factory=list)
    lead: list[dict] = field(default_factory=list)
    lead_allocate_record: list[dict] = field(default_factory=list)
    lead_distribute_record: list[dict] = field(default_factory=list)
    lead_follow_record: list[dict] = field(default_factory=list)
    manual_outbound_call: list[dict] = field(default_factory=list)
    ai_outbound_call: list[dict] = field(default_factory=list)
    test_drive_car: list[dict] = field(default_factory=list)
    test_drive_route: list[dict] = field(default_factory=list)
    test_drive: list[dict] = field(default_factory=list)
    chat_record: list[dict] = field(default_factory=list)
    recording: list[dict] = field(default_factory=list)  # synthetic (fix 3)


# ════════════════════════════════════════════════════════════════════════
# ID / value generators (ASCII identifiers, Chinese business text)
# ════════════════════════════════════════════════════════════════════════


def _ascii_id(prefix: str, n: int) -> str:
    """Stable ASCII id like 'L00000001' (zero-padded)."""
    return f"{prefix}{n:08d}"


def _phone(rng: random.Random) -> str:
    # Chinese mobile: 1 + [3-9] + 9 digits
    second = rng.choice("3456789")
    rest = "".join(rng.choices(string.digits, k=9))
    return f"1{second}{rest}"


def _vin(rng: random.Random) -> str:
    # 17-char VIN-ish (ASCII letters+digits, no I/O/Q)
    pool = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
    return "".join(rng.choices(pool, k=17))


def _plate(rng: random.Random) -> str:
    # Chinese plate: 京A12345
    province = rng.choice("京沪粤浙苏鲁川豫")
    letter = rng.choice(string.ascii_uppercase)
    alnum = "".join(rng.choices(string.digits + string.ascii_uppercase, k=5))
    return f"{province}{letter}{alnum}"


def _recording_id(source_table: str, url: str) -> str:
    """Fix 3: synthetic recording_id = sha1(source:url)[:16]. Deterministic."""
    return hashlib.sha1(f"{source_table}:{url}".encode()).hexdigest()[:16]


def _recording_url(source_table: str, n: int) -> str:
    return f"https:// recordings.{source_table}.cn/{_ascii_id('REC', n)}.mp3"


# ════════════════════════════════════════════════════════════════════════
# Data generation
# ════════════════════════════════════════════════════════════════════════


def generate_all(faker: Faker, rng: random.Random) -> GeneratedData:
    """Generate all rows deterministically. Returns a GeneratedData container."""
    data = GeneratedData()

    # ── dealership (20) ───────────────────────────────────────────────
    regions = ["华东大区", "华北大区", "华南大区", "西南大区", "华中大区"]
    for i in range(1, VOLUMES["dealership"] + 1):
        province = faker.province()
        data.dealership.append(
            {
                "store_code": _ascii_id("D", i),
                "org_name": f"{faker.company_prefix()}{rng.choice(['汽车', '新能源', '智行'])}4S店",
                "store_type": rng.choice(["直营店", "加盟店", "旗舰店"]),
                "store_status": rng.choice(["营业中", "筹备中", "已退网"]),
                "description": faker.sentence(nb_words=6),
                "store_categories": rng.choice(["乘用车", "商用车", "新能源"]),
                "store_level": rng.choice(["一级", "二级", "三级"]),
                "regional_sales": rng.choice(regions),
                "after_sales_region": rng.choice(regions),
                "province": province,
                "city": faker.city_name(),
                "area": faker.district(),
                "address": faker.address()[:80],
                "store_area": rng.randint(500, 3000),
                "opening_time": "09:00",
                "business_deadline": "18:00",
                "longitude": round(rng.uniform(73, 135), 6),
                "dimension": round(rng.uniform(18, 53), 6),
                "is_oversea": 0,
                "country": "中国",
            }
        )

    store_codes = [d["store_code"] for d in data.dealership]

    # ── sales_consultant (200, 10 per dealership) ────────────────────
    for i in range(1, VOLUMES["sales_consultant"] + 1):
        store = store_codes[(i - 1) // 10]
        data.sales_consultant.append(
            {
                "user_id": _ascii_id("S", i),
                "user_name": faker.name(),
                "phone": _phone(rng),
                "job_number": _ascii_id("JOB", i),
                "is_store_admin": 1 if (i % 10 == 1) else 0,
                "gender": rng.choice(["0", "1"]),
                "email": f"sales{i}@dealership.cn",
                "entry_time": faker.date_time_between(start_date="-3y", end_date="now"),
                "leave_status": "1",  # 在职
                "termination_time": None,
                "store_code": store,
                "status": "1",
                "create_time": faker.date_time_between(start_date="-3y", end_date="-2y"),
                "update_time": faker.date_time_between(start_date="-30d", end_date="now"),
            }
        )

    # ── lead_source (30, 4-level hierarchy) ──────────────────────────
    first_class = ["线上广告", "线下活动", "自然流量", "转介绍"]
    second_class_map = {
        "线上广告": ["搜索引擎", "信息流", "短视频"],
        "线下活动": ["车展", "商场展", "社区活动"],
        "自然流量": ["到店", "官网"],
        "转介绍": ["老客户转介", "员工内购"],
    }
    sid = 1
    for fc in first_class:
        for sc in second_class_map[fc]:
            data.lead_source.append(
                {
                    "source_id": _ascii_id("SRC", sid),
                    "show_name": f"{fc}-{sc}",
                    "source_level": str(rng.randint(1, 4)),
                    "parent_source_id": _ascii_id("SRC", rng.randint(1, max(1, sid - 1))) if sid > 4 else None,
                    "first_classification": fc,
                    "secondary_classification": sc,
                    "status": "1",
                    "create_time": ANCHOR_DATE - timedelta(days=365),
                    "update_time": ANCHOR_DATE - timedelta(days=30),
                }
            )
            sid += 1
    # pad to 30
    while len(data.lead_source) < VOLUMES["lead_source"]:
        data.lead_source.append(
            {
                "source_id": _ascii_id("SRC", sid),
                "show_name": f"渠道{sid}",
                "source_level": str(rng.randint(1, 4)),
                "parent_source_id": _ascii_id("SRC", rng.randint(1, sid - 1)),
                "first_classification": rng.choice(first_class),
                "secondary_classification": "其他",
                "status": "1",
                "create_time": ANCHOR_DATE - timedelta(days=365),
                "update_time": ANCHOR_DATE - timedelta(days=30),
            }
        )
        sid += 1

    # ── user (10,000) ─────────────────────────────────────────────────
    # Fix 4: user simplified to single source (t_ods_leads_server_leads_user_rt).
    # phone_brand / phone_device_model are modelled as always-null columns
    # (no CDP data populated) so L8 asserts they read back as null.
    for i in range(1, VOLUMES["user"] + 1):
        data.user.append(
            {
                "user_id": _ascii_id("U", i),
                "user_name": faker.name(),
                "mobile": _phone(rng),
                "reg_time": faker.date_time_between(start_date="-2y", end_date="now"),
                "phone_brand": None,
                "phone_device_model": None,
            }
        )

    # ── lead (10,000, 1:1 with user) ─────────────────────────────────
    source_ids = [s["source_id"] for s in data.lead_source]
    vehicle_series = ["智界S7", "问界M9", "智界R7", "问界M7", "享界S9"]
    vehicle_models = ["智界S7 Max", "问界M9 Ultra", "智界R7 Pro", "问界M7 Plus", "享界S9 Max"]
    provinces = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京"]
    # Pre-pick which leads test-drove (50%) so is_test_drive/test_drive_status consistent
    test_drive_lead_indices = set(rng.sample(range(VOLUMES["lead"]), VOLUMES["test_drive"]))
    for i in range(1, VOLUMES["lead"] + 1):
        is_td = i in test_drive_lead_indices
        # ~30% leads have next_follow_time on anchor date (for L2/L4/L7 date-prefix hits)
        if rng.random() < 0.3:
            next_follow = ANCHOR_DATE + timedelta(hours=rng.randint(0, 8))
        else:
            next_follow = faker.date_time_between(start_date="-30d", end_date="+30d")
        data.lead.append(
            {
                "id": _ascii_id("L", i),
                "leads_level": rng.choice(["H", "A", "B", "C"]),
                "filing_time": faker.date_time_between(start_date="-60d", end_date="now"),
                "filing_create_time": faker.date_time_between(start_date="-60d", end_date="now"),
                "four_source": rng.choice(source_ids),
                "data_source": str(rng.choice([1, 2, 3, 101])),
                "channel": rng.choice(["1", "2"]),
                "brand": "鸿蒙智行",
                "vehicle_model_name": rng.choice(vehicle_models),
                "vehicle_series_name": rng.choice(vehicle_series),
                "province": rng.choice(provinces),
                "city": faker.city_name(),
                "address": faker.address()[:60],
                "dealer_name": rng.choice(data.dealership)["org_name"],
                "dealer_code": rng.choice(store_codes),
                "leads_status": LEADS_STATUS_VALID,
                "receive_time": faker.date_time_between(start_date="-60d", end_date="-30d"),
                "first_send_time": faker.date_time_between(start_date="-30d", end_date="-20d"),
                "first_assign_time": faker.date_time_between(start_date="-20d", end_date="-10d"),
                "first_follow_time": faker.date_time_between(start_date="-10d", end_date="-5d"),
                "next_follow_time": next_follow,
                "claim_status": "1",
                "nick": faker.user_name(),
                "init_shop_code": rng.choice(store_codes),
                "stage": rng.choice(["1", "2"]),
                "last_follow_time": faker.date_time_between(start_date="-5d", end_date="now"),
                "last_follow_content": faker.sentence(nb_words=8),
                "is_allopatry": rng.choice([0, 1]),
                "test_drive": "1" if is_td else "0",
                "test_drive_status": "1" if is_td else "0",
                "lead_mark": rng.choice(["0", "1", "2"]),
                "status": "1",
                "creator": _ascii_id("S", rng.randint(1, VOLUMES["sales_consultant"])),
                "user_id": _ascii_id("U", i),
            }
        )

    lead_ids = [ld["id"] for ld in data.lead]
    consultant_ids = [s["user_id"] for s in data.sales_consultant]

    # ── lead_allocate_record (30,000, ~3 ops per lead: 下发/分配/转移 or 回收) ─
    rec_n = 0
    for li, lead_id in enumerate(lead_ids):
        sc = consultant_ids[li % len(consultant_ids)]  # primary assignee
        # op 1: 下发
        rec_n += 1
        data.lead_allocate_record.append(
            {
                "oid": _ascii_id("AR", rec_n),
                "operation_time": faker.date_time_between(start_date="-30d", end_date="-20d"),
                "leads_id": lead_id,
                "sales_consultant_id": sc,
                "type": "1",
                "first_flag": "1",
                "creator": "system",
                "create_time": faker.date_time_between(start_date="-30d", end_date="-20d"),
                "updater": "system",
                "update_time": faker.date_time_between(start_date="-20d", end_date="now"),
            }
        )
        # op 2: 分配
        rec_n += 1
        data.lead_allocate_record.append(
            {
                "oid": _ascii_id("AR", rec_n),
                "operation_time": faker.date_time_between(start_date="-20d", end_date="-10d"),
                "leads_id": lead_id,
                "sales_consultant_id": sc,
                "type": "2",
                "first_flag": "1",
                "creator": sc,
                "create_time": faker.date_time_between(start_date="-20d", end_date="-10d"),
                "updater": sc,
                "update_time": faker.date_time_between(start_date="-10d", end_date="now"),
            }
        )
        # op 3: 转移 or 回收 (50/50)
        rec_n += 1
        if rng.random() < 0.5:
            new_sc = rng.choice([c for c in consultant_ids if c != sc])
            data.lead_allocate_record.append(
                {
                    "oid": _ascii_id("AR", rec_n),
                    "operation_time": faker.date_time_between(start_date="-10d", end_date="now"),
                    "leads_id": lead_id,
                    "sales_consultant_id": new_sc,
                    "type": "3",
                    "first_flag": "0",
                    "creator": sc,
                    "create_time": faker.date_time_between(start_date="-10d", end_date="now"),
                    "updater": "system",
                    "update_time": faker.date_time_between(start_date="-5d", end_date="now"),
                }
            )
        else:
            data.lead_allocate_record.append(
                {
                    "oid": _ascii_id("AR", rec_n),
                    "operation_time": faker.date_time_between(start_date="-10d", end_date="now"),
                    "leads_id": lead_id,
                    "sales_consultant_id": sc,
                    "type": "4",
                    "first_flag": "0",
                    "creator": "system",
                    "create_time": faker.date_time_between(start_date="-10d", end_date="now"),
                    "updater": "system",
                    "update_time": faker.date_time_between(start_date="-5d", end_date="now"),
                }
            )
        if rec_n >= VOLUMES["lead_allocate_record"]:
            break

    # ── lead_distribute_record (15,000) — 下发到门店 ─────────────────
    for i in range(1, VOLUMES["lead_distribute_record"] + 1):
        lead = rng.choice(lead_ids)
        data.lead_distribute_record.append(
            {
                "oid": _ascii_id("DR", i),
                "operation_time": faker.date_time_between(start_date="-30d", end_date="-15d"),
                "leads_id": lead,
                "dealer_code": rng.choice(store_codes),
                "type": "1",
                "first_flag": "1" if i <= VOLUMES["lead"] else "0",
                "creator": "system",
                "create_time": faker.date_time_between(start_date="-30d", end_date="-15d"),
                "updater": "system",
                "update_time": faker.date_time_between(start_date="-15d", end_date="now"),
            }
        )

    # ── lead_follow_record (30,000, ~3 per lead) — Fix 2: snake_case cols ─
    fr_n = 0
    for lead_id in lead_ids:
        for _ in range(3):
            fr_n += 1
            data.lead_follow_record.append(
                {
                    "oid": _ascii_id("FR", fr_n),
                    "leadsinfoid": lead_id,
                    "follower_id": rng.choice(consultant_ids),
                    "follow_purpose": rng.choice(["邀约到店", "试驾安排", "价格沟通", "促成下单", "回访关怀"]),
                    "communication_methods": rng.choice(["电话", "微信", "到店", "短信"]),
                    "follow_result": rng.choice(["已接通", "未接通", "已到店", "已试驾", "意向明确", "暂无意向"]),
                    "follow_content": faker.sentence(nb_words=10),
                    "intended_level": rng.choice(["H", "A", "B", "C"]),
                    "vehicle_model_code": rng.choice(vehicle_models),
                    "vehicle_model_name": rng.choice(vehicle_models),
                    "business_no": _ascii_id("ORD", fr_n) if rng.random() < 0.1 else None,
                    "next_follow_time": faker.date_time_between(start_date="-5d", end_date="+10d"),
                    "follow_shop_id": rng.choice(store_codes),
                    "arrive_time": (
                        faker.date_time_between(start_date="-5d", end_date="now") if rng.random() < 0.3 else None
                    ),
                    "defeat_type": rng.choice(["价格", "竞品", "无需求", ""]) if rng.random() < 0.2 else "",
                    "change_business_opportunity": "1" if rng.random() < 0.2 else "0",
                    "creator": rng.choice(consultant_ids),
                    "create_time": faker.date_time_between(start_date="-30d", end_date="now"),
                }
            )
            if fr_n >= VOLUMES["lead_follow_record"]:
                break
        if fr_n >= VOLUMES["lead_follow_record"]:
            break

    # ── manual_outbound_call (20,000) ─────────────────────────────────
    # Fix 3: recording_id (stored as original_record_url) = synthetic _recording_id
    moc_rec_urls = []
    for i in range(1, VOLUMES["manual_outbound_call"] + 1):
        lead = rng.choice(lead_ids)
        url = _recording_url("manual_outbound_call", i)
        moc_rec_urls.append(url)
        data.manual_outbound_call.append(
            {
                "id": _ascii_id("MOC", i),
                "call_status": rng.choice(["1", "0"]),  # 1=接通
                "call_time": faker.date_time_between(start_date="-30d", end_date="now"),
                "call_duration": rng.randint(0, 600),
                "original_record_url": _recording_id("manual_outbound_call", url),
                "lead_id": lead,
                "user_id": _ascii_id("U", int(lead[1:])) if lead.startswith("L") else None,
            }
        )

    # ── ai_outbound_call (10,000) ─────────────────────────────────────
    for i in range(1, VOLUMES["ai_outbound_call"] + 1):
        lead = rng.choice(lead_ids)
        url = _recording_url("ai_outbound_call", i)
        data.ai_outbound_call.append(
            {
                "id": _ascii_id("AOC", i),
                "ai_tag_name": rng.choice(["高意向", "中意向", "低意向", "无意向"]),
                "task_name": f"AI外呼任务{rng.randint(1, 5)}",
                "call_duration_sec": rng.randint(0, 300),
                "call_status": rng.choice(["接通", "未接通"]),
                "call_times": faker.date_time_between(start_date="-30d", end_date="now"),
                "call_start_time": faker.date_time_between(start_date="-30d", end_date="now"),
                "call_end_time": faker.date_time_between(start_date="-30d", end_date="now"),
                "is_review": "0",
                "customer_name": faker.name(),
                "cellphone": _phone(rng),
                "leads_info_id": lead,
                "robot_id": _ascii_id("ROBOT", rng.randint(1, 5)),
                "audio_link_url": _recording_id("ai_outbound_call", url),
                "tenant_id": "tenant_001",
                "update_time": faker.date_time_between(start_date="-5d", end_date="now"),
            }
        )

    # ── test_drive_car (200, 10 per dealership) ──────────────────────
    for i in range(1, VOLUMES["test_drive_car"] + 1):
        store = store_codes[(i - 1) // 10]
        data.test_drive_car.append(
            {
                "id": _ascii_id("TDC", i),
                "car_model_vin": _vin(rng),
                "store_code": store,
                "car_model_name": rng.choice(vehicle_models),
                "number_plate": _plate(rng),
                "series_code": rng.choice(["S7", "M9", "R7", "M7", "S9"]),
                "car_series_name": rng.choice(vehicle_series),
                "model_code": rng.choice(vehicle_models),
                "model_name": rng.choice(vehicle_models),
                "qr_code_url": f"https://qr.cn/{_ascii_id('QR', i)}",
                "car_status": "1",
                "status": "1",
                "creator": "system",
                "creator_name": "系统",
                "create_time": faker.date_time_between(start_date="-1y", end_date="-6m"),
                "updater": "system",
                "updater_name": "系统",
                "update_time": faker.date_time_between(start_date="-30d", end_date="now"),
                "ds_src": f"mysql.{SCHEMA}.t_ods_test_drive_car_model",
            }
        )

    # ── test_drive_route (100, 5 per dealership) ─────────────────────
    for i in range(1, VOLUMES["test_drive_route"] + 1):
        store = store_codes[(i - 1) // 5]
        data.test_drive_route.append(
            {
                "id": _ascii_id("TDR", i),
                "route_name": f"{rng.choice(['城市', '高速', '山路', '环线'])}试驾路线{i}",
                "store_code": store,
                "is_enable": "1",
                "status": "1",
                "creator": "system",
                "creator_name": "系统",
                "create_time": faker.date_time_between(start_date="-1y", end_date="-6m"),
                "updater": "system",
                "updater_name": "系统",
                "update_time": faker.date_time_between(start_date="-30d", end_date="now"),
                "ds_src": f"mysql.{SCHEMA}.t_ods_test_drive_route",
            }
        )

    # ── test_drive (5,000, ~50% of leads) ────────────────────────────
    # Fix 1: NO test_drive_consultant_id; only sale_id → sales_consultant.
    # Fix 3: original_record_url stores synthetic recording_id.
    td_rec_urls = []
    td_lead_ids = [lid for i, lid in enumerate(lead_ids, 1) if i in test_drive_lead_indices]
    for i, lead_id in enumerate(td_lead_ids[: VOLUMES["test_drive"]], 1):
        lead = data.lead[i - 1]
        url = _recording_url("test_drive", i)
        td_rec_urls.append(url)
        # 60% completed (order_status 4/5/6), 40% pending (1/2/3)
        order_status = rng.choice(ORDER_STATUS_DONE) if rng.random() < 0.6 else rng.choice(ORDER_STATUS_PENDING)
        if order_status in ORDER_STATUS_DONE:
            end_time = faker.date_time_between(start_date="-20d", end_date="now")
        else:
            end_time = None
        data.test_drive.append(
            {
                "id": _ascii_id("TD", i),
                "end_time": end_time,
                "begin_time": end_time - timedelta(minutes=rng.randint(20, 90)) if end_time else None,
                "sale_id": rng.choice(consultant_ids),
                "original_record_url": _recording_id("test_drive", url) if url else None,
                "user_id": lead["user_id"],
                "leads_id": lead_id,
                "name": lead["nick"],
                "phone": data.user[int(lead["user_id"][1:]) - 1]["mobile"],
                "test_drive_type": rng.choice(["0", "1"]),
                "store_code": rng.choice(store_codes),
                "test_drive_id": None,  # NOT a consultant id (fix 1: removed consultant field)
                "schedule_time": faker.date_time_between(start_date="-10d", end_date="+10d"),
                "test_drive_date": faker.date_time_between(start_date="-10d", end_date="+10d"),
                "test_drive_time_period_id": str(rng.randint(1, 8)),
                "route_id": rng.choice([r["id"] for r in data.test_drive_route]),
                "test_drive_car_id": rng.choice([c["id"] for c in data.test_drive_car]),
                "door_time": None,
                "door_address": None,
                "duration": rng.randint(20, 90),
                "kilometre": round(rng.uniform(5, 30), 1),
                "order_status": order_status,
                "track_matching": rng.choice(["0", "1", "2"]),
                "effective_status": rng.choice(["0", "1", "2"]),
                "test_drive_class": rng.choice(["0", "1", "2"]),
                "test_drive_source": rng.choice(["1", "2", "3", "4"]),
                "first_test_drive_date": str(rng.randint(1, 8)),
                "follow_flag": rng.choice(["0", "1"]),
                "record_flag": "1" if url else "0",
                "record_url": url,
                "analysis_result_id": None,
                "record_type": rng.choice(["1", "2"]),
                "intended_car_series": rng.choice(vehicle_series),
                "status": "1",
                "creator": "system",
                "update_time": faker.date_time_between(start_date="-5d", end_date="now"),
            }
        )

    # ── chat_record (20,000) ─────────────────────────────────────────
    for i in range(1, VOLUMES["chat_record"] + 1):
        data.chat_record.append(
            {
                "id": _ascii_id("CR", i),
                "user_id": rng.choice([u["user_id"] for u in data.user[:2000]]),  # subset of users have wechat
                "record_type": rng.choice(["text", "image", "voice"]),
                "dialoguecontent": faker.sentence(nb_words=12),
                "createtime": faker.date_time_between(start_date="-30d", end_date="now"),
                "status": "1",
                "chat_deadline": faker.date_time_between(start_date="-5d", end_date="now"),
                "log_time": faker.date_time_between(start_date="-5d", end_date="now"),
            }
        )

    # ── recording (SYNTHETIC, fix 3) — merged from 3 url sources ──────
    seen: set[str] = set()
    for url in td_rec_urls + moc_rec_urls:
        rid = _recording_id(
            "test_drive" if url in td_rec_urls else "manual_outbound_call",
            url,
        )
        if rid in seen:
            continue
        seen.add(rid)
        data.recording.append(
            {
                "recording_id": rid,
                "recording_url": url,
                "recording_text": None,  # not populated (DESIGN.md fix 3)
            }
        )
    # ai_outbound_call recordings
    for i in range(1, VOLUMES["ai_outbound_call"] + 1):
        url = _recording_url("ai_outbound_call", i)
        rid = _recording_id("ai_outbound_call", url)
        if rid in seen:
            continue
        seen.add(rid)
        data.recording.append(
            {
                "recording_id": rid,
                "recording_url": url,
                "recording_text": None,
            }
        )

    return data


# ════════════════════════════════════════════════════════════════════════
# DDL + persistence
# ════════════════════════════════════════════════════════════════════════

# DDL lives in a sibling file for readability.
DDL_FILE = Path(__file__).resolve().parent / "marketing_schema.sql"


async def _exec(cur, sql: str) -> None:
    await cur.execute(sql)


# Entity (GeneratedData attr) → physical MySQL table name.
# Most entities share their name with the physical table; the two leads_operation
# records share one physical table (t_ods_source_data_leads_operation_record).
ENTITY_TO_TABLE: dict[str, str] = {
    "dealership": "t_ods_master_data_store",
    "sales_consultant": "t_ods_master_data_staff",
    "lead_source": "t_ods_leads_server_leads_source",
    "user": "t_ods_leads_server_leads_user_rt",
    "lead": "t_ods_leads_server_leads_info_rt",
    "lead_allocate_record": "t_ods_source_data_leads_operation_record",
    "lead_distribute_record": "t_ods_source_data_leads_operation_record",
    "lead_follow_record": "t_ods_source_data_leads_follow_record",
    "manual_outbound_call": "t_ods_leads_server_sale_call_record_rt",
    "ai_outbound_call": "t_ods_leads_server_ai_call_out_result_rt",
    "test_drive_car": "t_ods_test_drive_car_model",
    "test_drive_route": "t_ods_test_drive_route",
    "test_drive": "t_ods_test_drive_test_drive_rt",
    "chat_record": "t_ods_inspection_weixin_log",
    "recording": "recording",
}


async def _insert_batch(cur, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(f"`{c}`" for c in cols)
    sql = f"INSERT INTO `{SCHEMA}`.`{table}` ({col_list}) VALUES ({placeholders})"
    # Batch in chunks of 1000
    batch_size = 1000
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        await cur.executemany(sql, [[r.get(c) for c in cols] for r in chunk])


async def seed_database(conn_kwargs: dict, drop: bool = False) -> GeneratedData:
    """Create schema + tables, generate data, insert. Returns the generated data."""
    faker = Faker(["zh_CN"])
    Faker.seed(RANDOM_SEED)
    rng = random.Random(RANDOM_SEED)

    logger.info("Generating %d tables of data (seed=%d)...", len(VOLUMES), RANDOM_SEED)
    data = generate_all(faker, rng)
    total = sum(len(getattr(data, f)) for f in data.__dataclass_fields__)
    logger.info("Generated %d total rows.", total)

    logger.info("Connecting to MySQL %s@%s:%s ...", conn_kwargs["user"], conn_kwargs["host"], conn_kwargs["port"])
    conn = await aiomysql.connect(
        host=conn_kwargs["host"],
        port=conn_kwargs["port"],
        user=conn_kwargs["user"],
        password=conn_kwargs["password"],
        db=None,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        async with conn.cursor() as cur:
            if drop:
                logger.info("Dropping schema `%s` (if exists)...", SCHEMA)
                await cur.execute(f"DROP DATABASE IF EXISTS `{SCHEMA}`")
            await cur.execute(f"CREATE DATABASE IF NOT EXISTS `{SCHEMA}` DEFAULT CHARSET utf8mb4")
            await cur.execute(f"USE `{SCHEMA}`")

            logger.info("Applying DDL from %s ...", DDL_FILE.name)
            ddl = DDL_FILE.read_text(encoding="utf-8")
            # Strip `--` line comments, then split on `;` at statement boundaries.
            # (Naive split on `;` breaks when comment text contains periods/newlines.)
            stripped_lines = [line for line in ddl.splitlines() if not line.strip().startswith("--")]
            cleaned = "\n".join(stripped_lines)
            for stmt in cleaned.split(";"):
                stmt = stmt.strip()
                if stmt:
                    await cur.execute(stmt)

            # Insert in FK-dependency order
            order = [
                "dealership",
                "sales_consultant",
                "lead_source",
                "user",
                "lead",
                "lead_allocate_record",
                "lead_distribute_record",
                "lead_follow_record",
                "manual_outbound_call",
                "ai_outbound_call",
                "test_drive_car",
                "test_drive_route",
                "test_drive",
                "chat_record",
                "recording",
            ]
            for tbl in order:
                rows = getattr(data, tbl)
                phys_table = ENTITY_TO_TABLE.get(tbl, tbl)
                logger.info("  %-26s → %-42s %6d rows", tbl, phys_table, len(rows))
                await _insert_batch(cur, phys_table, rows)

        logger.info("✓ Seeding complete.")
        for tbl in order:
            logger.info("  %-26s → %-42s %6d", tbl, ENTITY_TO_TABLE.get(tbl, tbl), len(getattr(data, tbl)))
    finally:
        conn.close()

    return data


def _conn_kwargs_from_env() -> dict:
    return {
        "host": os.environ.get("MARKETING_MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("MARKETING_MYSQL_PORT", "3306")),
        "user": os.environ.get("MARKETING_MYSQL_USER", "root"),
        "password": os.environ.get("MARKETING_MYSQL_PASSWORD", ""),
    }


def main() -> None:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Seed Marketing benchmark source MySQL data.")
    p.add_argument("--host", default=os.environ.get("MARKETING_MYSQL_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MARKETING_MYSQL_PORT", "3306")))
    p.add_argument("--user", default=os.environ.get("MARKETING_MYSQL_USER", "root"))
    p.add_argument("--password", default=os.environ.get("MARKETING_MYSQL_PASSWORD", ""))
    p.add_argument("--drop", action="store_true", help="Drop & recreate schema before seeding (idempotent re-runs).")
    args = p.parse_args()

    asyncio.run(seed_database(vars(args), drop=args.drop))


if __name__ == "__main__":
    main()
