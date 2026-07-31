"""DVP benchmark — fixture seeder (writes the MySQL source database).

Generates the source-side MySQL physical data for the DVP benchmark with a
FIXED seed (RANDOM_SEED=42) and Faker zh_CN locale (DESIGN.md §2.4). This is
the **seeding exception** (DESIGN.md §5.1, principle 3.5): benchmark may
write directly to the source DB before the system under test starts. No
Iceberg/Doris/SeaTunnel involvement — DVP is all VIRTUAL (Trino federates to
this MySQL at query time).

Usage:
    MARKETING_MYSQL_PASSWORD=... python -m tests.benchmark.dvp.scripts.seed_dvp [--drop]

Connection params default to a local MySQL (the same marketing-mysql container
is reused — DVP just uses a different database ``dvp_benchmark``). Override
via env DVP_MYSQL_*.

Topology (DESIGN.md §5.1): rows are generated in FK topological order so
every child row's FK points at a real parent:
    project_base → project_vehicle → vehicle_body → structures(5) → component
    project_base → project_target → lms_target_dimension → lms_target_iteration
    project_base → lms_project
    project_base → dvp_design → experiment_item_round
    oper_condition → oper_condition_detail(4 types) → test_item
    spec → lms_trial_standard
    change_point_entity (on component) → (M:N to oper_condition via triggers,
        but that's a link not a physical FK — no join table seeded; the link
        is expressed at query time through change_point↔operCondition traversal,
        which for VIRTUAL is best modelled as a filter on a shared attribute.
        For benchmark simplicity we DO NOT seed a change_point↔oper_condition
        join table; L3/L4 etc. use the DVP-plan-based path instead.)

Volumes (DESIGN.md §2.3):
    project_base 20, project_vehicle 60, lms_project 20, project_target 40,
    lms_target_dimension 2000, lms_target_iteration 6000, vehicle_body 60,
    front/side/rear/chassis_structure 60 each, exterior_design 120,
    component 30000, change_point_entity 10000, oper_condition 30,
    oper_condition_detail 200 (4×50), test_item 30000, spec 500,
    lms_trial_standard 500, dvp_design 20, experiment_item_round 200.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("seed_dvp")

RANDOM_SEED = 42

# ── Volumes (DESIGN.md §2.3) ─────────────────────────────────────────────
VOLUMES: dict[str, int] = {
    "project_base": 20,
    "project_vehicle": 60,
    "lms_project": 20,
    "project_target": 40,
    "lms_target_dimension": 2000,
    "lms_target_iteration": 6000,
    "vehicle_body": 60,
    "front_structure": 60,
    "side_structure": 60,
    "rear_structure": 60,
    "chassis_structure": 60,
    "exterior_design": 120,
    "component": 30000,
    "change_point_entity": 10000,
    "oper_condition": 30,
    "oper_condition_detail": 200,
    "test_item": 30000,
    "spec": 500,
    "lms_trial_standard": 500,
    "dvp_design": 20,
    "experiment_item_round": 200,
}

# ── Enum dictionaries (physical stores CODE, display is Chinese via ontology) ─
PROJECT_STATUS = {"1": "立项中", "2": "开发中", "3": "已冻结", "4": "已归档"}
STATUS_ACTIVE = "1"      # generic active status (1=有效/待执行, see per-entity)
STATUS_TODO = "1"        # test_item: 1=待执行 2=执行中 3=已完成 4=已取消
DEV_TIERS = ["T1", "T2", "T3", "T4"]
POWER_TYPES = ["燃油", "插电混动", "纯电动", "增程式"]
DRIVE_TYPES = ["前驱", "后驱", "四驱"]
TARGET_MARKETS = ["国内", "欧洲", "北美", "东南亚"]
BRANDS = ["领航", "远征", "星辰", "海纳", "云驰", "天工", "锐行", "瀚海"]
DEV_PHASES = ["ET1", "ET2", "PT1", "PT2", "SOP"]
CONDITION_TYPES = ["front_collision", "rear_collision", "side_collision", "pedestrian_protect"]
CONDITION_CATEGORIES = ["正面碰撞", "侧面碰撞", "尾部碰撞", "行人保护", "NVH", "操稳"]
STRUCTURE_TYPES = ["front", "side", "rear", "chassis", "exterior"]
CHANGE_TYPES = ["设计变更", "材料变更", "工艺变更", "供应商变更"]
CHANGE_DEGREES_RANGE = (1, 5)  # inclusive


@dataclass
class GeneratedData:
    """All generated rows, keyed by table-name (matches VOLUMES keys)."""
    project_base: list[dict] = field(default_factory=list)
    project_vehicle: list[dict] = field(default_factory=list)
    lms_project: list[dict] = field(default_factory=list)
    project_target: list[dict] = field(default_factory=list)
    lms_target_dimension: list[dict] = field(default_factory=list)
    lms_target_iteration: list[dict] = field(default_factory=list)
    vehicle_body: list[dict] = field(default_factory=list)
    front_structure: list[dict] = field(default_factory=list)
    side_structure: list[dict] = field(default_factory=list)
    rear_structure: list[dict] = field(default_factory=list)
    chassis_structure: list[dict] = field(default_factory=list)
    exterior_design: list[dict] = field(default_factory=list)
    component: list[dict] = field(default_factory=list)
    change_point_entity: list[dict] = field(default_factory=list)
    oper_condition: list[dict] = field(default_factory=list)
    oper_condition_detail: list[dict] = field(default_factory=list)
    test_item: list[dict] = field(default_factory=list)
    spec: list[dict] = field(default_factory=list)
    lms_trial_standard: list[dict] = field(default_factory=list)
    dvp_design: list[dict] = field(default_factory=list)
    experiment_item_round: list[dict] = field(default_factory=list)


# ── ID helpers (ASCII identifiers, DESIGN.md §2.4) ───────────────────────
def _proj_code(rng: random.Random, n: int) -> str:
    return f"P2024{n:03d}"


def _vehicle_code(rng: random.Random, proj_n: int, v_n: int) -> str:
    return f"V{proj_n:02d}{v_n:02d}"


def _lms_project_id(n: int) -> str:
    return f"LMS-P{n:03d}"


def _target_code(proj_n: int, t_n: int) -> str:
    return f"TGT-P{proj_n:02d}{t_n:02d}"


def _dimension_id(n: int) -> str:
    return f"DIM-{n:05d}"


def _iteration_id(n: int) -> str:
    return f"ITR-{n:05d}"


def _body_code(v_n: int) -> str:
    return f"BODY-{v_n:04d}"


def _structure_code(prefix: str, b_n: int) -> str:
    return f"{prefix}-{b_n:04d}"


def _component_id(n: int) -> str:
    return f"CMP-{n:05d}"


def _change_point_id(n: int) -> str:
    return f"CP-{n:06d}"


def _condition_code(cat: str, n: int) -> str:
    return f"OC-{cat[:2].upper()}-{n:03d}"


def _detail_condition_code(ctype: str, n: int) -> str:
    return f"DC-{ctype[:3].upper()}-{n:03d}"


def _test_item_id(n: int) -> str:
    return f"TI-{n:06d}"


def _spec_code(n: int) -> str:
    return f"STD-{n:04d}"


def _standard_id(n: int) -> str:
    return f"LTS-{n:04d}"


def _dvp_code(proj_n: int) -> str:
    return f"DVP-P{proj_n:02d}"


def _round_id(dvp_n: int, r_n: int) -> str:
    return f"RND-{dvp_n:02d}{r_n:02d}"


# ── Time helpers (deterministic, seeded) ─────────────────────────────────
def _date_in_2026(rng: random.Random, start_month: int = 1, end_month: int = 12) -> str:
    """Return a 'YYYY-MM-DD' date in 2026 within [start_month, end_month]."""
    month = rng.randint(start_month, end_month)
    day = rng.randint(1, 28)
    return f"2026-{month:02d}-{day:02d}"


def _datetime_in_2026(rng: random.Random) -> str:
    """Return a 'YYYY-MM-DD HH:MM:SS' datetime in 2026."""
    return f"{_date_in_2026(rng)} {rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"


# ═══════════════════════════════════════════════════════════════════════════
# Generation — topological order
# ═══════════════════════════════════════════════════════════════════════════

def generate_all(faker, rng: random.Random) -> GeneratedData:
    """Generate all 21 tables in FK topological order. Deterministic w/ seed."""
    data = GeneratedData()
    now_base = "2026-06-15 10:00:00"

    # ── project_base (20) ────────────────────────────────────────────────
    for n in range(1, VOLUMES["project_base"] + 1):
        pcode = _proj_code(rng, n)
        data.project_base.append({
            "project_code": pcode,
            "project_name": f"{rng.choice(BRANDS)}·{faker.company_prefix()}{n}项目",  # type: ignore[attr-defined]
            "brand": rng.choice(BRANDS),
            "project_type": rng.choice(["全新开发", "改款", "换代"]),
            "dev_tier": rng.choice(DEV_TIERS),
            "lifecycle_state": rng.choice(["概念", "开发", "验证", "量产"]),
            "project_status": rng.choice(list(PROJECT_STATUS.keys())),
            "manager_name": faker.name(),
            "research_unit": faker.company() + "研究院",
            "project_start_time": _date_in_2026(rng, 1, 3),
            "plan_end_time": _date_in_2026(rng, 10, 12),
            "approval_date": _date_in_2026(rng, 1, 6),
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
            "create_by": faker.name(),
            "update_by": faker.name(),
            "delete_mark": 0,
        })

    # ── project_vehicle (60: 3 per project) ─────────────────────────────
    for pn in range(1, VOLUMES["project_base"] + 1):
        pcode = _proj_code(rng, pn)
        for vn in range(1, 4):  # 3 vehicles per project
            vcode = _vehicle_code(rng, pn, vn)
            data.project_vehicle.append({
                "vehicle_code": vcode,
                "project_code": pcode,
                "vehicle_name": f"{rng.choice(BRANDS)}车型{pn}-{vn}",
                "power_type": rng.choice(POWER_TYPES),
                "drive_type": rng.choice(DRIVE_TYPES),
                "dev_tier": rng.choice(DEV_TIERS),
                "target_market": rng.choice(TARGET_MARKETS),
                "vehicle_category": rng.choice(["轿车", "SUV", "MPV", "轿跑"]),
                "development_method": rng.choice(["自主", "联合开发", "委托"]),
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
                "create_by": faker.name(),
                "update_by": faker.name(),
            })

    # ── lms_project (20: 1 per project_base) ────────────────────────────
    for n in range(1, VOLUMES["project_base"] + 1):
        pcode = _proj_code(rng, n)
        data.lms_project.append({
            "lms_project_id": _lms_project_id(n),
            "project_code": pcode,
            "bom_id": f"BOM-{n:04d}",
            "sample_car_sys_id": f"SCS-{n:04d}",
            "lms_project_name": f"LMS集成-{pcode}",
            "sync_status": rng.choice(["已同步", "待同步", "同步失败"]),
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })

    # ── project_target (40: 2 per project) ──────────────────────────────
    for pn in range(1, VOLUMES["project_base"] + 1):
        pcode = _proj_code(rng, pn)
        for tn in range(1, 3):
            data.project_target.append({
                "target_code": _target_code(pn, tn),
                "project_code": pcode,
                "target_title": f"{rng.choice(['安全', '性能', 'NVH', '可靠性'])}目标{pn}-{tn}",
                "target_category": rng.choice(["安全", "性能", "舒适", "环保"]),
                "target_description": faker.sentence(nb_words=10),
                "target_response_dept": faker.company() + "部",
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
                "create_by": faker.name(),
            })

    # ── lms_target_dimension (2000: 50 per target) ──────────────────────
    dim_n = 0
    for tgt in data.project_target:
        for k in range(50):
            dim_n += 1
            data.lms_target_dimension.append({
                "dimension_id": _dimension_id(dim_n),
                "target_code": tgt["target_code"],
                "dimension_title": f"量化指标{dim_n}",
                "dimension_category": tgt["target_category"],
                "target_threshold": f"{rng.randint(10, 500)}{rng.choice(['mm', 'g', 'dB', 'kN'])}",
                "response_unit": tgt["target_response_dept"],
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
            })

    # ── lms_target_iteration (6000: 3 per dimension) ────────────────────
    itr_n = 0
    for dim in data.lms_target_dimension:
        for v in range(1, 4):
            itr_n += 1
            data.lms_target_iteration.append({
                "iteration_id": _iteration_id(itr_n),
                "dimension_id": dim["dimension_id"],
                "iteration_version": f"v{v}.0",
                "iteration_date": _date_in_2026(rng, 1, 12),
                "iteration_threshold": f"{rng.randint(10, 500)}{rng.choice(['mm', 'g', 'dB', 'kN'])}",
                "change_note": faker.sentence(nb_words=8) if v > 1 else "初始版本",
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
            })

    # ── vehicle_body (60: 1 per project_vehicle) ───────────────────────
    for vn, veh in enumerate(data.project_vehicle, 1):
        data.vehicle_body.append({
            "body_code": _body_code(vn),
            "vehicle_code": veh["vehicle_code"],
            "body_name": f"车身-{veh['vehicle_name']}",
            "vehicle_weight": round(rng.uniform(1200.0, 2200.0), 1),
            "body_form": rng.choice(["三厢", "两厢", "SUV", "轿跑"]),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })

    # ── 4 structure tables (60 each: 1 per body) + exterior_design (120) ─
    # Each body gets one of each structure type. exterior gets 2 per body.
    for bn, body in enumerate(data.vehicle_body, 1):
        bcode = body["body_code"]
        data.front_structure.append({
            "front_structure_code": _structure_code("FR", bn),
            "body_code": bcode,
            "front_structure_name": f"前部结构-{bn}",
            "front_rail_form": rng.choice(["直梁", "弧形梁", "液压成型"]),
            "energy_box_type": rng.choice(["铝合金", "钢制", "复合材料"]),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })
        data.side_structure.append({
            "side_structure_code": _structure_code("SD", bn),
            "body_code": bcode,
            "side_structure_name": f"侧面结构-{bn}",
            "b_pillar_form": rng.choice(["热成型钢", "超高强钢", "铝合金"]),
            "sill_form": rng.choice(["一体式", "分体式"]),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })
        data.rear_structure.append({
            "rear_structure_code": _structure_code("RR", bn),
            "body_code": bcode,
            "rear_structure_name": f"尾部结构-{bn}",
            "rear_rail_form": rng.choice(["直梁", "弧形梁"]),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })
        data.chassis_structure.append({
            "chassis_structure_code": _structure_code("CH", bn),
            "body_code": bcode,
            "chassis_structure_name": f"底盘结构-{bn}",
            "suspension_form": rng.choice(["麦弗逊", "双叉臂", "多连杆"]),
            "steering_form": rng.choice(["电动助力", "液压助力"]),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })
        for en in range(1, 3):  # 2 exteriors per body
            data.exterior_design.append({
                "exterior_code": _structure_code(f"EX{en}", bn),
                "body_code": bcode,
                "exterior_name": f"外饰{en}-{bn}",
                "exterior_type": rng.choice(["保险杠", "大灯", "格栅", "侧围"]),
                "stiffness_param": f"{rng.randint(100, 900)}MPa",
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
            })

    # ── component (30000: ~500 per body, spread across 5 structure types) ─
    # Build a lookup: body_code → list of (structure_code, structure_type).
    body_structures: list[tuple[str, str]] = []  # (structure_code, structure_type)
    for s in data.front_structure:
        body_structures.append((s["front_structure_code"], "front"))
    for s in data.side_structure:
        body_structures.append((s["side_structure_code"], "side"))
    for s in data.rear_structure:
        body_structures.append((s["rear_structure_code"], "rear"))
    for s in data.chassis_structure:
        body_structures.append((s["chassis_structure_code"], "chassis"))
    for s in data.exterior_design:
        body_structures.append((s["exterior_code"], "exterior"))
    # 60 bodies × 4 standard structures + 60×2 exteriors = 360 structure rows.
    # Target 30000 components: 100 per standard structure (60×4×100=24000)
    # + 50 per exterior structure (120×50=6000) = 30000.
    comp_n = 0
    for struct_code, struct_type in body_structures:
        per = 50 if struct_type == "exterior" else 100
        for k in range(per):
            comp_n += 1
            data.component.append({
                "component_id": _component_id(comp_n),
                "structure_code": struct_code,
                "structure_type": struct_type,
                "component_name": f"{rng.choice(['纵梁', '横梁', '吸能盒', 'B柱', '门槛', '防撞梁', '副车架', '悬架臂', '保险杠', '大灯'])}-{comp_n}",
                "component_category": rng.choice(["结构件", "安全件", "外饰件", "底盘件"]),
                "spec_model": f"M{rng.randint(1000, 9999)}",
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
                "create_by": faker.name(),
            })

    # ── change_point_entity (10000: spread across components, ~1 per 3) ───
    for cp_n in range(1, VOLUMES["change_point_entity"] + 1):
        comp = rng.choice(data.component)
        data.change_point_entity.append({
            "change_point_id": _change_point_id(cp_n),
            "component_id": comp["component_id"],
            "change_description": f"{comp['component_name']}的{rng.choice(['尺寸', '材料', '工艺', '结构'])}变更",
            "change_degree": rng.randint(*CHANGE_DEGREES_RANGE),
            "weight": round(rng.uniform(0.1, 1.0), 2),
            "change_type": rng.choice(CHANGE_TYPES),
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
            "create_by": faker.name(),
        })

    # ── oper_condition (30: ~7-8 per category) ──────────────────────────
    cats = CONDITION_CATEGORIES
    for n in range(1, VOLUMES["oper_condition"] + 1):
        cat = cats[(n - 1) % len(cats)]
        data.oper_condition.append({
            "condition_code": _condition_code(cat, n),
            "condition_name": f"{cat}工况{n}",
            "condition_description": f"{cat}试验场景{n}的详细描述",
            "condition_category": cat,
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })

    # ── oper_condition_detail (200: 50 per condition_type, linked to oper_condition) ─
    # Map condition_type → a pool of oper_condition codes of matching category.
    type_to_cat = {
        "front_collision": "正面碰撞",
        "rear_collision": "尾部碰撞",
        "side_collision": "侧面碰撞",
        "pedestrian_protect": "行人保护",
    }
    detail_n = 0
    for ctype in CONDITION_TYPES:
        cat = type_to_cat[ctype]
        matching_ocs = [oc["condition_code"] for oc in data.oper_condition if oc["condition_category"] == cat]
        if not matching_ocs:
            # fallback: use any oper_condition
            matching_ocs = [oc["condition_code"] for oc in data.oper_condition]
        for k in range(50):
            detail_n += 1
            data.oper_condition_detail.append({
                "detail_condition_code": _detail_condition_code(ctype, k + 1),
                "condition_code": rng.choice(matching_ocs),
                "condition_type": ctype,
                "detail_condition_name": f"{cat}细分工况{k + 1}",
                "test_description": f"{cat}测试场景{k + 1}：{faker.sentence(nb_words=6)}",
                "status": STATUS_ACTIVE,
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
            })

    # ── spec (500) ──────────────────────────────────────────────────────
    for n in range(1, VOLUMES["spec"] + 1):
        data.spec.append({
            "spec_code": _spec_code(n),
            "spec_name": f"试验规范{n}",
            "applicable_model": rng.choice(["轿车", "SUV", "MPV", "通用"]),
            "test_preparation": faker.sentence(nb_words=10),
            "operation_steps": faker.sentence(nb_words=12),
            "equipment_requirement": f"{rng.choice(['碰撞假人', '加速度传感器', '高速摄像机', '测力墙'])}等",
            "pass_threshold": f"{rng.randint(10, 500)}{rng.choice(['mm', 'g', 'dB', 'kN'])}",
            "status": rng.choice(["1", "2", "3"]),  # 草稿/已发布/作废
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
            "create_by": faker.name(),
        })

    # ── lms_trial_standard (500: 1 per spec) ────────────────────────────
    for n, sp in enumerate(data.spec, 1):
        data.lms_trial_standard.append({
            "standard_id": _standard_id(n),
            "spec_code": sp["spec_code"],
            "standard_name": f"LMS标准-{sp['spec_name']}",
            "test_cost": round(rng.uniform(1.0, 50.0), 2),  # 万元
            "total_cost": round(rng.uniform(1.0, 80.0), 2),
            "external_quote": round(rng.uniform(0.5, 30.0), 2),
            "sample_count": rng.randint(1, 20),
            "work_hours": rng.randint(8, 200),
            "equipment_list": f"{rng.choice(['碰撞假人', '传感器', '摄像机'])}×{rng.randint(1, 10)}",
            "test_period": rng.randint(1, 60),  # days
            "status": sp["status"],
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
        })

    # ── dvp_design (20: 1 per project_base) ─────────────────────────────
    for n, proj in enumerate(data.project_base, 1):
        data.dvp_design.append({
            "dvp_code": _dvp_code(n),
            "project_code": proj["project_code"],
            "dvp_name": f"DVP计划-{proj['project_code']}",
            "plan_start_time": _date_in_2026(rng, 1, 4),
            "plan_end_time": _date_in_2026(rng, 9, 12),
            "test_budget": round(rng.uniform(100.0, 2000.0), 2),  # 万元
            "sample_car_count": rng.randint(5, 50),
            "resource_allocation": f"{rng.choice(['试验中心A', '试验中心B', '外部机构'])}+{rng.randint(3, 15)}人",
            "status": STATUS_ACTIVE,
            "create_time": _datetime_in_2026(rng),
            "update_time": _datetime_in_2026(rng),
            "create_by": faker.name(),
        })

    # ── experiment_item_round (200: 10 per dvp_design) ──────────────────
    rnd_n = 0
    for dvp in data.dvp_design:
        for k in range(1, 11):
            rnd_n += 1
            oc = rng.choice(data.oper_condition)
            data.experiment_item_round.append({
                "round_id": _round_id(rnd_n, k),
                "dvp_code": dvp["dvp_code"],
                "condition_code": oc["condition_code"],
                "round_name": f"轮次{dvp['dvp_code']}-{k}",
                "dev_phase": rng.choice(DEV_PHASES),
                "milestone": f"MS-{k}",
                "round_start_time": _date_in_2026(rng, 1, 6),
                "round_end_time": _date_in_2026(rng, 7, 12),
                "status": rng.choice([STATUS_ACTIVE, "2", "3"]),
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
            })

    # ── test_item (30000: ~150 per detail_condition) ────────────────────
    # Each detail_condition gets ~150 test_items. Link some to dimensions & specs
    # (but leave some spec_code NULL for L5 LEFT JOIN coverage). Link each to
    # exactly one dimension_id (for verifiesTarget).
    dim_ids = [d["dimension_id"] for d in data.lms_target_dimension]
    spec_codes = [s["spec_code"] for s in data.spec]
    ti_n = 0
    for detail in data.oper_condition_detail:
        dcode = detail["detail_condition_code"]
        for k in range(150):
            ti_n += 1
            # 80% have a spec, 20% NULL (for L5 LEFT JOIN null coverage)
            sc = rng.choice(spec_codes) if rng.random() < 0.8 else None
            data.test_item.append({
                "test_item_id": _test_item_id(ti_n),
                "detail_condition_code": dcode,
                "dimension_id": rng.choice(dim_ids),
                "spec_code": sc,
                "test_item_name": f"试验项{ti_n}",
                "sample_count": rng.randint(1, 10),
                "evaluation_criteria": faker.sentence(nb_words=6),
                "prep_period": rng.randint(1, 30),  # days
                "test_response": faker.name(),
                "status": rng.choice([STATUS_TODO, "2", "3", "4"]),  # 待执行/执行中/已完成/已取消
                "plan_end_time": _date_in_2026(rng, 6, 12),
                "create_time": _datetime_in_2026(rng),
                "update_time": _datetime_in_2026(rng),
                "create_by": faker.name(),
            })

    return data


# ═══════════════════════════════════════════════════════════════════════════
# Database writing
# ═══════════════════════════════════════════════════════════════════════════

SCHEMA = "dvp_benchmark"
HERE = Path(__file__).resolve().parent
DDL_FILE = HERE / "dvp_schema.sql"

# Insert order (FK topological). Each name matches a GeneratedData field +
# a physical table (with the t_ prefix added below).
INSERT_ORDER: list[str] = [
    "project_base",
    "project_vehicle",
    "lms_project",
    "project_target",
    "lms_target_dimension",
    "lms_target_iteration",
    "vehicle_body",
    "front_structure",
    "side_structure",
    "rear_structure",
    "chassis_structure",
    "exterior_design",
    "component",
    "change_point_entity",
    "oper_condition",
    "oper_condition_detail",
    "spec",
    "lms_trial_standard",
    "dvp_design",
    "experiment_item_round",
    "test_item",
]

# GeneratedData field name → physical table name.
FIELD_TO_TABLE: dict[str, str] = {f: f"t_{f}" for f in INSERT_ORDER}


async def _insert_batch(cur, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(f"`{c}`" for c in cols)
    sql = f"INSERT INTO `{SCHEMA}`.`{table}` ({col_list}) VALUES ({placeholders})"
    batch_size = 1000
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        await cur.executemany(sql, [[r.get(c) for c in cols] for r in chunk])


async def seed_database(conn_kwargs: dict, drop: bool = False) -> GeneratedData:
    """Create schema + tables, generate data, insert. Returns the generated data."""
    try:
        from faker import Faker
    except ImportError as e:
        sys.exit(f"Faker is required for seeding: {e}. Install with `uv sync --extra dev`.")
    try:
        import aiomysql
    except ImportError as e:
        sys.exit(f"aiomysql is required for seeding: {e}.")

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
            stripped = [ln for ln in ddl.splitlines() if not ln.strip().startswith("--")]
            cleaned = "\n".join(stripped)
            for stmt in cleaned.split(";"):
                stmt = stmt.strip()
                if stmt:
                    await cur.execute(stmt)

            for field in INSERT_ORDER:
                rows = getattr(data, field)
                table = FIELD_TO_TABLE[field]
                logger.info("  %-26s → %-30s %6d rows", field, table, len(rows))
                await _insert_batch(cur, table, rows)

        logger.info("✓ Seeded %d tables into `%s`.", len(INSERT_ORDER), SCHEMA)
    finally:
        conn.close()
    return data


def _conn_kwargs_from_env() -> dict:
    return {
        "host": os.environ.get("DVP_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DVP_MYSQL_PORT", "3306")),
        "user": os.environ.get("DVP_MYSQL_USER", "root"),
        "password": os.environ.get("DVP_MYSQL_PASSWORD", "marketing123"),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Seed DVP benchmark source MySQL data.")
    parser.add_argument("--drop", action="store_true", help="DROP DATABASE first (idempotent re-seed).")
    args = parser.parse_args()
    asyncio.run(seed_database(_conn_kwargs_from_env(), drop=args.drop))


if __name__ == "__main__":
    main()
