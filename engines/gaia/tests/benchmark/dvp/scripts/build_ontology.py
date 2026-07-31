"""DVP ontology generator — produces dvp-ontology.json.

This is the **single source of truth** for the DVP benchmark ontology.
Edit the Python data structures below (not the generated JSON) to evolve the
ontology. Run::

    python -m tests.benchmark.dvp.scripts.build_ontology

to regenerate ``data/ontology/dvp-ontology.json``.

Design contract (see DESIGN.md §3):
  - ObjectTypes are PascalCase (caller-supplied).
  - storage_type = VIRTUAL for ALL types (Trino federates to MySQL; no Iceberg/
    Doris). The Gravitino MySQL catalog is registered via the DataSource API;
    backing_mapping points at the registered VIRTUAL dataset api_name.
  - Property apiNames are derived by the backend from backing_column
    (snake_case) — we only supply display_name (Chinese) + backing_mapping.
    Identifiers (PK/FK/code) stay ASCII; business text is Chinese
    (DESIGN.md §2.4).
  - No "attributed relationships" (per user instruction): all links are plain
    LinkTypeDef (source/target/cardinality/direction/foreign_key). Context
    that used to live on a link is either a real entity property or not
    modelled.
  - 4 sub-condition ObjectTypes (FrontCollision/RearCollision/
    SideCollision/PedestrianProtect) share physical table
    ``t_oper_condition_detail``, distinguished by ``condition_type``. Each is
    registered as its own VIRTUAL dataset (same table, same backing) — the
    condition_type filter is applied at query time, not at registration.

Output schema aligns with OntologyCreate + ObjectTypeBatchCreate
(src/ontology/core/schemas/ontology.py), which the setup script
(01_setup_ontology.py) will consume. There are NO action_types (DVP does not
test the write path — all VIRTUAL tables are read-only).
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Physical catalog/schema (the MySQL source database) ──────────────────
# The catalog name is the Gaia DataSource api_name (registered by
# 01_setup_ontology.py via POST /api/datasources with connector_type="mysql").
# IMPORTANT: this MUST equal the DataSource api_name (camelCase, e.g. "dvpMysql"),
# because ObjectQueryService._resolve_trino_catalog matches backing_catalog
# case-insensitively against registered DataSources and returns the
# DataSource api_name (Trino lower-cases it on catalog registration, so the
# real Trino catalog is "dvpmysql"). A snake_case value like "dvp_mysql"
# would NOT match ("dvp_mysql" != "dvpmysql") and yield CATALOG_NOT_FOUND.
CATALOG = "dvpMysql"
SCHEMA = "dvp_benchmark"

# ── Data type helper ─────────────────────────────────────────────────────
DT = str  # one of DataType StrEnum values


def prop(
    display_name: str,
    data_type: str,
    backing_column: str,
    *,
    table: str,
    dataset_api_name: str,
    description: str = "",
    is_primary_key: bool = False,
    is_title_property: bool = False,
    searchable: bool = True,
) -> dict:
    """Build a PropertyInput dict.

    apiName is derived by the backend from backing_column (snake_case) →
    camelCase. We pass display_name (Chinese) + backing_mapping only. The
    setup script post-processes backing_mapping to fill dataset_api_name
    (the registered VIRTUAL dataset) — but we also set it here for clarity.
    """
    return {
        "display_name": display_name,
        "description": description,
        "data_type": data_type,
        "searchable": searchable,
        "is_primary_key": is_primary_key or None,
        "is_title_property": is_title_property or None,
        "backing_mapping": {
            "backing_catalog": CATALOG,
            "backing_schema": SCHEMA,
            "backing_table": table,
            "backing_column": backing_column,
            "dataset_api_name": dataset_api_name,
        },
    }


def link(
    display_name: str,
    target: str,
    cardinality: str = "MANY",
    direction: str = "OUTGOING",
    *,
    api_name: str,
    description: str = "",
) -> dict:
    """Build a LinkInput dict.

    ``api_name`` is caller-supplied camelCase (links have no backing_column,
    so Chinese display_name has no ASCII anchor — must supply api_name
    explicitly to avoid the linkTypeN fallback). ``target`` is the target
    ObjectType api_name (PascalCase).
    """
    return {
        "display_name": display_name,
        "api_name": api_name,
        "description": description,
        "target_object_type_id": target,
        "cardinality": cardinality,
        "direction": direction,
    }


def ot(
    api_name: str,
    display_name: str,
    description: str,
    properties: list[dict],
    links: list[dict] | None = None,
    *,
    primary_key: str | None = None,
    title_property: str | None = None,
) -> dict:
    """Build an ObjectTypeBatchCreate dict (storage_type=VIRTUAL for all)."""
    d: dict = {
        "api_name": api_name,
        "display_name": display_name,
        "description": description,
        "storage_type": "VIRTUAL",
        "primary_key": primary_key,
        "title_property": title_property,
        "properties": properties,
        "links": links or [],
    }
    return d


# ═══════════════════════════════════════════════════════════════════════════
# Domain 1: 项目与目标 (Project & Target)
# ═══════════════════════════════════════════════════════════════════════════

PROJECT_BASE = ot(
    "ProjectBase",
    "项目基准",
    "研发项目本身，试验验证活动的组织单元，顶层父容器。",
    [
        prop("项目令号", DT("STRING"), "project_code", table="t_project_base",
             dataset_api_name="project_base", is_primary_key=True,
             description="项目业务编码，企业通用"),
        prop("项目名称", DT("STRING"), "project_name", table="t_project_base",
             dataset_api_name="project_base", is_title_property=True),
        prop("品牌", DT("STRING"), "brand", table="t_project_base",
             dataset_api_name="project_base"),
        prop("项目类型", DT("STRING"), "project_type", table="t_project_base",
             dataset_api_name="project_base"),
        prop("开发层级", DT("STRING"), "dev_tier", table="t_project_base",
             dataset_api_name="project_base"),
        prop("生命周期状态", DT("STRING"), "lifecycle_state",
             table="t_project_base", dataset_api_name="project_base"),
        prop("项目状态", DT("STRING"), "project_status",
             table="t_project_base", dataset_api_name="project_base"),
        prop("项目经理", DT("STRING"), "manager_name",
             table="t_project_base", dataset_api_name="project_base"),
        prop("承研单位", DT("STRING"), "research_unit",
             table="t_project_base", dataset_api_name="project_base"),
        prop("项目启动时间", DT("DATE"), "project_start_time",
             table="t_project_base", dataset_api_name="project_base"),
        prop("计划完成时间", DT("DATE"), "plan_end_time",
             table="t_project_base", dataset_api_name="project_base"),
        prop("审批日期", DT("DATE"), "approval_date",
             table="t_project_base", dataset_api_name="project_base"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_project_base", dataset_api_name="project_base"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_project_base", dataset_api_name="project_base"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_project_base", dataset_api_name="project_base"),
        prop("更新人", DT("STRING"), "update_by",
             table="t_project_base", dataset_api_name="project_base"),
        prop("逻辑删除标记", DT("BOOLEAN"), "delete_mark",
             table="t_project_base", dataset_api_name="project_base"),
    ],
    links=[
        # Link 1: projectBase → projectVehicle (MANY, contains)
        # 业务链路1: 项目包含车辆项目（父→子）
        link("包含车辆项目", "ProjectVehicle", "MANY", "OUTGOING",
             api_name="containsVehicle", description="项目基准包含多个车辆项目"),
        # Link 18: projectBase → lmsProject (ONE, syncs to)
        link("同步到LMS", "LmsProject", "ONE", "OUTGOING",
             api_name="syncsToLms", description="项目同步到 LMS 实验室集成项目"),
    ],
    primary_key="projectCode",
    title_property="projectName",
)


PROJECT_VEHICLE = ot(
    "ProjectVehicle",
    "车辆项目",
    "项目基准下的基础执行单元，定义车辆技术特征，关联物理车身与试验体系。",
    [
        prop("车辆项目编码", DT("STRING"), "vehicle_code",
             table="t_project_vehicle", dataset_api_name="project_vehicle",
             is_primary_key=True),
        prop("所属项目令号", DT("STRING"), "project_code",
             table="t_project_vehicle", dataset_api_name="project_vehicle",
             description="FK → projectBase.project_code"),
        prop("车型名称", DT("STRING"), "vehicle_name",
             table="t_project_vehicle", dataset_api_name="project_vehicle",
             is_title_property=True),
        prop("动力类型", DT("STRING"), "power_type",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("驱动方式", DT("STRING"), "drive_type",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("开发等级", DT("STRING"), "dev_tier",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("目标市场", DT("STRING"), "target_market",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("车辆类别", DT("STRING"), "vehicle_category",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("开发方式", DT("STRING"), "development_method",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("状态", DT("STRING"), "status",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
        prop("更新人", DT("STRING"), "update_by",
             table="t_project_vehicle", dataset_api_name="project_vehicle"),
    ],
    links=[
        # Link 2: projectVehicle → vehicleBody (ONE, contains)
        # 业务链路1: 车辆项目包含车身（父→子，1车型1车身）
        link("包含车身", "VehicleBody", "ONE", "OUTGOING",
             api_name="containsBody", description="车辆项目包含车身"),
        # 链路5: projectVehicle → lmsProject (ONE, syncs to)
        link("同步到LMS", "LmsProject", "ONE", "OUTGOING",
             api_name="vehicleSyncsToLms", description="车辆项目同步到 LMS"),
    ],
    primary_key="vehicleCode",
    title_property="vehicleName",
)


LMS_PROJECT = ot(
    "LmsProject",
    "LMS集成项目",
    "对接外部 LMS/实验室管理系统的集成对象，承载全项目信息用于数据互通。",
    [
        prop("LMS项目ID", DT("STRING"), "lms_project_id",
             table="t_lms_project", dataset_api_name="lms_project",
             is_primary_key=True),
        prop("所属项目令号", DT("STRING"), "project_code",
             table="t_lms_project", dataset_api_name="lms_project",
             description="FK → projectBase.project_code"),
        prop("BOM标识", DT("STRING"), "bom_id",
             table="t_lms_project", dataset_api_name="lms_project"),
        prop("样车管理系统标识", DT("STRING"), "sample_car_sys_id",
             table="t_lms_project", dataset_api_name="lms_project"),
        prop("LMS项目名称", DT("STRING"), "lms_project_name",
             table="t_lms_project", dataset_api_name="lms_project",
             is_title_property=True),
        prop("同步状态", DT("STRING"), "sync_status",
             table="t_lms_project", dataset_api_name="lms_project"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_lms_project", dataset_api_name="lms_project"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_lms_project", dataset_api_name="lms_project"),
    ],
    primary_key="lmsProjectId",
    title_property="lmsProjectName",
)


PROJECT_TARGET = ot(
    "ProjectTarget",
    "项目目标",
    "项目顶层业务总体要求，承载核心战略意图，驱动下层技术指标与试验项。",
    [
        prop("目标编号", DT("STRING"), "target_code",
             table="t_project_target", dataset_api_name="project_target",
             is_primary_key=True),
        prop("所属项目令号", DT("STRING"), "project_code",
             table="t_project_target", dataset_api_name="project_target",
             description="FK → projectBase.project_code"),
        prop("目标标题", DT("STRING"), "target_title",
             table="t_project_target", dataset_api_name="project_target",
             is_title_property=True),
        prop("目标类别", DT("STRING"), "target_category",
             table="t_project_target", dataset_api_name="project_target"),
        prop("目标描述", DT("STRING"), "target_description",
             table="t_project_target", dataset_api_name="project_target"),
        prop("责任部门", DT("STRING"), "target_response_dept",
             table="t_project_target", dataset_api_name="project_target"),
        prop("状态", DT("STRING"), "status",
             table="t_project_target", dataset_api_name="project_target"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_project_target", dataset_api_name="project_target"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_project_target", dataset_api_name="project_target"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_project_target", dataset_api_name="project_target"),
    ],
    links=[
        # Link 17: projectTarget → projectBase (MANY)
        link("所属项目", "ProjectBase", "MANY", "OUTGOING",
             api_name="targetBelongsToProject", description="项目目标属于项目基准"),
    ],
    primary_key="targetCode",
    title_property="targetTitle",
)


LMS_TARGET_DIMENSION = ot(
    "LmsTargetDimension",
    "LMS目标维度",
    "具象、可量化的考核条目，是项目目标的实例化拆分单元。",
    [
        prop("目标维度ID", DT("STRING"), "dimension_id",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension",
             is_primary_key=True),
        prop("所属目标编号", DT("STRING"), "target_code",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension",
             description="FK → projectTarget.target_code"),
        prop("目标维度标题", DT("STRING"), "dimension_title",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension",
             is_title_property=True),
        prop("目标类别", DT("STRING"), "dimension_category",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
        prop("目标阈值", DT("STRING"), "target_threshold",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
        prop("责任单位", DT("STRING"), "response_unit",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
        prop("状态", DT("STRING"), "status",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_lms_target_dimension", dataset_api_name="lms_target_dimension"),
    ],
    links=[
        # Link 16: lmsTargetDimension → projectTarget (MANY)
        link("汇总到目标", "ProjectTarget", "MANY", "OUTGOING",
             api_name="aggregatesTo", description="目标维度汇总到项目目标"),
        # Link: lmsTargetDimension → lmsTargetIteration (MANY)
        # 目标维度的版本迭代（一个维度有多次迭代版本）
        link("包含迭代", "LmsTargetIteration", "MANY", "OUTGOING",
             api_name="hasIteration", description="目标维度包含多次版本迭代"),
    ],
    primary_key="dimensionId",
    title_property="dimensionTitle",
)


LMS_TARGET_ITERATION = ot(
    "LmsTargetIteration",
    "目标迭代",
    "目标维度的版本迭代实体，记录不同阶段目标指标的变更、调整记录。",
    [
        prop("迭代ID", DT("STRING"), "iteration_id",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration",
             is_primary_key=True),
        prop("所属目标维度ID", DT("STRING"), "dimension_id",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration",
             description="FK → lmsTargetDimension.dimension_id"),
        prop("迭代版本号", DT("STRING"), "iteration_version",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("迭代日期", DT("DATE"), "iteration_date",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("迭代阈值", DT("STRING"), "iteration_threshold",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("变更说明", DT("STRING"), "change_note",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("状态", DT("STRING"), "status",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_lms_target_iteration", dataset_api_name="lms_target_iteration"),
    ],
    primary_key="iterationId",
    title_property="iterationVersion",
)


# ═══════════════════════════════════════════════════════════════════════════
# Domain 2: 车辆物理结构 (Vehicle Physical Structure)
# ═══════════════════════════════════════════════════════════════════════════

VEHICLE_BODY = ot(
    "VehicleBody",
    "车身",
    "整车基础载体，承载全部结构与零部件。",
    [
        prop("车身编码", DT("STRING"), "body_code",
             table="t_vehicle_body", dataset_api_name="vehicle_body",
             is_primary_key=True),
        prop("所属车辆项目编码", DT("STRING"), "vehicle_code",
             table="t_vehicle_body", dataset_api_name="vehicle_body",
             description="FK → projectVehicle.vehicle_code"),
        prop("车身名称", DT("STRING"), "body_name",
             table="t_vehicle_body", dataset_api_name="vehicle_body",
             is_title_property=True),
        prop("整车重量", DT("DOUBLE"), "vehicle_weight",
             table="t_vehicle_body", dataset_api_name="vehicle_body"),
        prop("外形形式", DT("STRING"), "body_form",
             table="t_vehicle_body", dataset_api_name="vehicle_body"),
        prop("状态", DT("STRING"), "status",
             table="t_vehicle_body", dataset_api_name="vehicle_body"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_vehicle_body", dataset_api_name="vehicle_body"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_vehicle_body", dataset_api_name="vehicle_body"),
    ],
    links=[
        # Link 2: vehicleBody → structures (ONE, contains) — 5 条各结构 OT 上声明
        # 业务链路1: 车身包含各子结构（父→子）
        link("包含前部结构", "FrontStructure", "ONE", "OUTGOING",
             api_name="containsFront", description="车身包含前部结构"),
        link("包含侧面结构", "SideStructure", "ONE", "OUTGOING",
             api_name="containsSide", description="车身包含侧面结构"),
        link("包含尾部结构", "RearStructure", "ONE", "OUTGOING",
             api_name="containsRear", description="车身包含尾部结构"),
        link("包含底盘结构", "ChassisStructure", "ONE", "OUTGOING",
             api_name="containsChassis", description="车身包含底盘结构"),
        link("包含外饰造型", "ExteriorDesign", "MANY", "OUTGOING",
             api_name="containsExterior", description="车身包含外饰造型（1车身多外饰方案）"),
    ],
    primary_key="bodyCode",
    title_property="bodyName",
)


# Helper to build a structure OT (front/side/rear/chassis share the same shape)
def _structure_ot(api_name: str, display_name: str, description: str,
                  table: str, dataset: str, code_col: str, name_col: str,
                  extra_props: list[dict], link_to_component_api: str) -> dict:
    """Build a structure ObjectType with standard shape + 1 outgoing link.

    - link_to_component_api: link apiName to Component (containsComponent)
    - The vehicleBody→structure 'contains' link is declared on VehicleBody
      (parent→child), NOT here. This OT only declares structure→component.
    """
    base_props = [
        prop("结构编码", DT("STRING"), code_col, table=table, dataset_api_name=dataset,
             is_primary_key=True),
        prop("所属车身编码", DT("STRING"), "body_code", table=table, dataset_api_name=dataset,
             description="FK → vehicleBody.body_code"),
        prop("结构名称", DT("STRING"), name_col, table=table, dataset_api_name=dataset,
             is_title_property=True),
    ]
    tail_props = [
        prop("状态", DT("STRING"), "status", table=table, dataset_api_name=dataset),
        prop("创建时间", DT("TIMESTAMP"), "create_time", table=table, dataset_api_name=dataset),
        prop("更新时间", DT("TIMESTAMP"), "update_time", table=table, dataset_api_name=dataset),
    ]
    return ot(
        api_name, display_name, description,
        base_props + extra_props + tail_props,
        links=[
            # Link 8: structure → component (MANY, contains)
            link("包含零部件", "Component", "MANY", "OUTGOING",
                 api_name=link_to_component_api, description=f"{display_name}包含若干零部件"),
        ],
        primary_key=None,  # derived from is_primary_key flag
        title_property=None,
    )


FRONT_STRUCTURE = _structure_ot(
    "FrontStructure", "前部结构",
    "车身前舱、防撞吸能核心结构：前纵梁、前防撞横梁、前吸能盒。设计直接影响正面碰撞安全性能。",
    table="t_front_structure", dataset="front_structure",
    code_col="front_structure_code", name_col="front_structure_name",
    extra_props=[
        prop("前纵梁形式", DT("STRING"), "front_rail_form",
             table="t_front_structure", dataset_api_name="front_structure"),
        prop("吸能盒类型", DT("STRING"), "energy_box_type",
             table="t_front_structure", dataset_api_name="front_structure"),
    ],
    link_to_component_api="frontContainsComponent",
)

SIDE_STRUCTURE = _structure_ot(
    "SideStructure", "侧面结构",
    "车身侧面碰撞防护结构：B柱、门槛、车门防撞梁。设计直接决定侧面碰撞保护能力。",
    table="t_side_structure", dataset="side_structure",
    code_col="side_structure_code", name_col="side_structure_name",
    extra_props=[
        prop("B柱形式", DT("STRING"), "b_pillar_form",
             table="t_side_structure", dataset_api_name="side_structure"),
        prop("门槛形式", DT("STRING"), "sill_form",
             table="t_side_structure", dataset_api_name="side_structure"),
    ],
    link_to_component_api="sideContainsComponent",
)

REAR_STRUCTURE = _structure_ot(
    "RearStructure", "尾部结构",
    "车尾防撞承载结构：后纵梁、后防撞横梁、后吸能盒。影响追尾碰撞安全表现。",
    table="t_rear_structure", dataset="rear_structure",
    code_col="rear_structure_code", name_col="rear_structure_name",
    extra_props=[
        prop("后纵梁形式", DT("STRING"), "rear_rail_form",
             table="t_rear_structure", dataset_api_name="rear_structure"),
    ],
    link_to_component_api="rearContainsComponent",
)

CHASSIS_STRUCTURE = _structure_ot(
    "ChassisStructure", "底盘结构",
    "整车行驶承载系统：前/后副车架、悬架、转向、制动。决定操控、NVH、底盘碰撞能量传递效率。",
    table="t_chassis_structure", dataset="chassis_structure",
    code_col="chassis_structure_code", name_col="chassis_structure_name",
    extra_props=[
        prop("悬架形式", DT("STRING"), "suspension_form",
             table="t_chassis_structure", dataset_api_name="chassis_structure"),
        prop("转向形式", DT("STRING"), "steering_form",
             table="t_chassis_structure", dataset_api_name="chassis_structure"),
    ],
    link_to_component_api="chassisContainsComponent",
)

EXTERIOR_DESIGN = ot(
    "ExteriorDesign",
    "外饰造型",
    "整车内外饰造型设计件：保险杠、大灯、格栅。外形、刚度直接影响行人保护、碰撞外观侵入量。",
    [
        prop("外饰编码", DT("STRING"), "exterior_code",
             table="t_exterior_design", dataset_api_name="exterior_design",
             is_primary_key=True),
        prop("所属车身编码", DT("STRING"), "body_code",
             table="t_exterior_design", dataset_api_name="exterior_design",
             description="FK → vehicleBody.body_code"),
        prop("外饰名称", DT("STRING"), "exterior_name",
             table="t_exterior_design", dataset_api_name="exterior_design",
             is_title_property=True),
        prop("外饰类型", DT("STRING"), "exterior_type",
             table="t_exterior_design", dataset_api_name="exterior_design"),
        prop("刚度参数", DT("STRING"), "stiffness_param",
             table="t_exterior_design", dataset_api_name="exterior_design"),
        prop("状态", DT("STRING"), "status",
             table="t_exterior_design", dataset_api_name="exterior_design"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_exterior_design", dataset_api_name="exterior_design"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_exterior_design", dataset_api_name="exterior_design"),
    ],
    links=[
        # Link 8: exteriorDesign → component (MANY, contains)
        link("包含零部件", "Component", "MANY", "OUTGOING",
             api_name="exteriorContainsComponent", description="外饰造型包含若干零部件"),
    ],
    primary_key="exteriorCode",
    title_property="exteriorName",
)

COMPONENT = ot(
    "Component",
    "零部件",
    "物理结构最小拆分单元，变化点分析的基础单元，所有变更最终落地到零部件粒度。",
    [
        prop("零部件编码", DT("STRING"), "component_id",
             table="t_component", dataset_api_name="component",
             is_primary_key=True),
        prop("所属结构编码", DT("STRING"), "structure_code",
             table="t_component", dataset_api_name="component",
             description="FK → 所属 structure 的 code（前/侧/尾/底盘/外饰）"),
        prop("所属结构类型", DT("STRING"), "structure_type",
             table="t_component", dataset_api_name="component",
             description="标识属于哪类结构：front/side/rear/chassis/exterior"),
        prop("零部件名称", DT("STRING"), "component_name",
             table="t_component", dataset_api_name="component",
             is_title_property=True),
        prop("零部件类别", DT("STRING"), "component_category",
             table="t_component", dataset_api_name="component"),
        prop("规格型号", DT("STRING"), "spec_model",
             table="t_component", dataset_api_name="component"),
        prop("状态", DT("STRING"), "status",
             table="t_component", dataset_api_name="component"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_component", dataset_api_name="component"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_component", dataset_api_name="component"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_component", dataset_api_name="component"),
    ],
    links=[
        # Link 9: component → changePointEntity (MANY)
        link("关联变化点", "ChangePointEntity", "MANY", "OUTGOING",
             api_name="hasChangePoint", description="零部件关联变化点"),
    ],
    primary_key="componentId",
    title_property="componentName",
)

CHANGE_POINT_ENTITY = ot(
    "ChangePointEntity",
    "变化点实体",
    "核心变更业务概念，描述一处实物/设计改动，作为是否需要新增/重跑试验的判断依据。",
    [
        prop("变化点ID", DT("STRING"), "change_point_id",
             table="t_change_point_entity", dataset_api_name="change_point_entity",
             is_primary_key=True),
        prop("所属零部件编码", DT("STRING"), "component_id",
             table="t_change_point_entity", dataset_api_name="change_point_entity",
             description="FK → component.component_id"),
        prop("变更描述", DT("STRING"), "change_description",
             table="t_change_point_entity", dataset_api_name="change_point_entity",
             is_title_property=True),
        prop("变更程度", DT("INTEGER"), "change_degree",
             table="t_change_point_entity", dataset_api_name="change_point_entity",
             description="变更程度等级 1-5，数值越大影响越大"),
        prop("权重", DT("DOUBLE"), "weight",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
        prop("变更类型", DT("STRING"), "change_type",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
        prop("状态", DT("STRING"), "status",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_change_point_entity", dataset_api_name="change_point_entity"),
    ],
    links=[
        # Link 10: changePointEntity → operCondition (MANY)
        link("触发试验工况", "OperCondition", "MANY", "OUTGOING",
             api_name="triggersCondition", description="变化点驱动受影响试验大类"),
    ],
    primary_key="changePointId",
    title_property="changeDescription",
)


# ═══════════════════════════════════════════════════════════════════════════
# Domain 3: 试验与验证 (Test & Verification)
# ═══════════════════════════════════════════════════════════════════════════

OPER_CONDITION = ot(
    "OperCondition",
    "试验工况大类",
    "试验顶层分类，用于归类所有测试场景（正面碰撞/侧面碰撞/行人保护/尾部低速刮擦等）。",
    [
        prop("工况编码", DT("STRING"), "condition_code",
             table="t_oper_condition", dataset_api_name="oper_condition",
             is_primary_key=True),
        prop("工况名称", DT("STRING"), "condition_name",
             table="t_oper_condition", dataset_api_name="oper_condition",
             is_title_property=True),
        prop("工况描述", DT("STRING"), "condition_description",
             table="t_oper_condition", dataset_api_name="oper_condition"),
        prop("工况大类", DT("STRING"), "condition_category",
             table="t_oper_condition", dataset_api_name="oper_condition"),
        prop("状态", DT("STRING"), "status",
             table="t_oper_condition", dataset_api_name="oper_condition"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_oper_condition", dataset_api_name="oper_condition"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_oper_condition", dataset_api_name="oper_condition"),
    ],
    links=[
        # Link 11: operCondition → 4 sub-conditions (MANY, declared once per target)
        link("包含正碰工况", "FrontCollision", "MANY", "OUTGOING",
             api_name="hasFrontDetail", description="工况大类含正面碰撞细分"),
        link("包含尾碰工况", "RearCollision", "MANY", "OUTGOING",
             api_name="hasRearDetail", description="工况大类含尾部碰撞细分"),
        link("包含侧碰工况", "SideCollision", "MANY", "OUTGOING",
             api_name="hasSideDetail", description="工况大类含侧面碰撞细分"),
        link("包含行人保护工况", "PedestrianProtect", "MANY", "OUTGOING",
             api_name="hasPedestrianDetail", description="工况大类含行人保护细分"),
    ],
    primary_key="conditionCode",
    title_property="conditionName",
)


def _sub_condition_ot(api_name: str, display_name: str, description: str,
                      condition_type_value: str,
                      contains_test_item_link_api: str) -> dict:
    """Build a sub-condition ObjectType sharing t_oper_condition_detail.

    All 4 sub-conditions point at the same physical table
    ``t_oper_condition_detail`` (same dataset_api_name ``oper_condition_detail``)
    and share the same backing columns. The ``condition_type`` column carries
    the discriminator value (front_collision/rear_collision/side_collision/
    pedestrian_protect). Query-time filters on condition_type isolate each
    OT's rows — DESIGN.md §3.4 修正4, L7/L13 回归覆盖.
    """
    return ot(
        api_name, display_name, description,
        [
            prop("细分工况编码", DT("STRING"), "detail_condition_code",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail",
                 is_primary_key=True),
            prop("所属工况编码", DT("STRING"), "condition_code",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail",
                 description="FK → operCondition.condition_code"),
            prop("工况类型", DT("STRING"), "condition_type",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail",
                 description=f"判别列，本 OT 恒为 '{condition_type_value}'"),
            prop("细分工况名称", DT("STRING"), "detail_condition_name",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail",
                 is_title_property=True),
            prop("测试描述", DT("STRING"), "test_description",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail"),
            prop("状态", DT("STRING"), "status",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail"),
            prop("创建时间", DT("TIMESTAMP"), "create_time",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail"),
            prop("更新时间", DT("TIMESTAMP"), "update_time",
                 table="t_oper_condition_detail", dataset_api_name="oper_condition_detail"),
        ],
        links=[
            # Link 12: sub-condition → testItem (MANY)
            # (reverse traversal to OperCondition uses OperCondition's
            #  hasFrontDetail/hasRearDetail/... outgoing links — no need to
            #  declare a redundant belongsToCondition here.)
            # NOTE: link api_name must be unique within the ontology — the 4
            # sub-conditions each get a distinct containsTestItem variant.
            link("包含试验项", "TestItem", "MANY", "OUTGOING",
                 api_name=contains_test_item_link_api, description="细分工况包含试验项"),
        ],
        primary_key="detailConditionCode",
        title_property="detailConditionName",
    )


FRONT_COLLISION = _sub_condition_ot(
    "FrontCollision", "正面碰撞工况",
    "正面碰撞细分工况：50km/h 刚性壁障正面撞击。",
    condition_type_value="front_collision",
    contains_test_item_link_api="frontContainsTestItem",
)
REAR_COLLISION = _sub_condition_ot(
    "RearCollision", "尾部碰撞工况",
    "尾部碰撞细分工况：追尾低速碰撞。",
    condition_type_value="rear_collision",
    contains_test_item_link_api="rearContainsTestItem",
)
SIDE_COLLISION = _sub_condition_ot(
    "SideCollision", "侧面碰撞工况",
    "侧面碰撞细分工况：移动壁障侧面撞击。",
    condition_type_value="side_collision",
    contains_test_item_link_api="sideContainsTestItem",
)
PEDESTRIAN_PROTECT = _sub_condition_ot(
    "PedestrianProtect", "行人保护工况",
    "行人保护细分工况：头型/腿型冲击器撞击。",
    condition_type_value="pedestrian_protect",
    contains_test_item_link_api="pedestrianContainsTestItem",
)

TEST_ITEM = ot(
    "TestItem",
    "试验项",
    "单个工况下需要执行的具体测量、检测条目，关联项目目标维度验证指标是否达标。",
    [
        prop("试验项ID", DT("STRING"), "test_item_id",
             table="t_test_item", dataset_api_name="test_item",
             is_primary_key=True),
        prop("所属细分工况编码", DT("STRING"), "detail_condition_code",
             table="t_test_item", dataset_api_name="test_item",
             description="FK → 细分工况 detail_condition_code"),
        prop("验证目标维度ID", DT("STRING"), "dimension_id",
             table="t_test_item", dataset_api_name="test_item",
             description="FK → lmsTargetDimension.dimension_id"),
        prop("规范编码", DT("STRING"), "spec_code",
             table="t_test_item", dataset_api_name="test_item",
             description="FK → spec.spec_code (nullable)"),
        prop("试验项名称", DT("STRING"), "test_item_name",
             table="t_test_item", dataset_api_name="test_item",
             is_title_property=True),
        prop("样本量", DT("INTEGER"), "sample_count",
             table="t_test_item", dataset_api_name="test_item"),
        prop("评价标准", DT("STRING"), "evaluation_criteria",
             table="t_test_item", dataset_api_name="test_item"),
        prop("试验准备周期", DT("INTEGER"), "prep_period",
             table="t_test_item", dataset_api_name="test_item"),
        prop("测试负责人", DT("STRING"), "test_response",
             table="t_test_item", dataset_api_name="test_item"),
        prop("状态", DT("STRING"), "status",
             table="t_test_item", dataset_api_name="test_item",
             description="待执行/执行中/已完成等"),
        prop("计划完成时间", DT("DATE"), "plan_end_time",
             table="t_test_item", dataset_api_name="test_item"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_test_item", dataset_api_name="test_item"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_test_item", dataset_api_name="test_item"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_test_item", dataset_api_name="test_item"),
    ],
    links=[
        # Link 13: testItem → spec (MANY)
        link("引用规范", "Spec", "MANY", "OUTGOING",
             api_name="referencesSpec", description="试验项引用试验规范"),
        # Link 15: testItem → lmsTargetDimension (MANY)
        link("验证目标维度", "LmsTargetDimension", "MANY", "OUTGOING",
             api_name="verifiesTarget", description="试验项验证量化目标维度"),
    ],
    primary_key="testItemId",
    title_property="testItemName",
)

SPEC = ot(
    "Spec",
    "试验规范",
    "试验操作、判定标准的完整文档载体，是试验执行、结果判定的唯一依据。",
    [
        prop("规范编码", DT("STRING"), "spec_code",
             table="t_spec", dataset_api_name="spec",
             is_primary_key=True),
        prop("规范名称", DT("STRING"), "spec_name",
             table="t_spec", dataset_api_name="spec",
             is_title_property=True),
        prop("适用车型", DT("STRING"), "applicable_model",
             table="t_spec", dataset_api_name="spec"),
        prop("试验准备", DT("STRING"), "test_preparation",
             table="t_spec", dataset_api_name="spec"),
        prop("操作步骤", DT("STRING"), "operation_steps",
             table="t_spec", dataset_api_name="spec"),
        prop("设备要求", DT("STRING"), "equipment_requirement",
             table="t_spec", dataset_api_name="spec"),
        prop("合格阈值", DT("STRING"), "pass_threshold",
             table="t_spec", dataset_api_name="spec"),
        prop("状态", DT("STRING"), "status",
             table="t_spec", dataset_api_name="spec",
             description="草稿/已发布/作废"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_spec", dataset_api_name="spec"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_spec", dataset_api_name="spec"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_spec", dataset_api_name="spec"),
    ],
    links=[
        # Link 14: spec → lmsTrialStandard (ONE)
        link("扩展为LMS标准", "LmsTrialStandard", "ONE", "OUTGOING",
             api_name="extendsTrialStandard", description="规范扩展为 LMS 试验标准"),
    ],
    primary_key="specCode",
    title_property="specName",
)

LMS_TRIAL_STANDARD = ot(
    "LmsTrialStandard",
    "LMS试验标准",
    "规范的扩展实体，集成更多管理属性：试验成本、人力、设备资源、试验周期，对接实验室系统。",
    [
        prop("LMS标准ID", DT("STRING"), "standard_id",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard",
             is_primary_key=True),
        prop("所属规范编码", DT("STRING"), "spec_code",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard",
             description="FK → spec.spec_code"),
        prop("标准名称", DT("STRING"), "standard_name",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard",
             is_title_property=True),
        prop("试验成本", DT("DOUBLE"), "test_cost",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("总成本", DT("DOUBLE"), "total_cost",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("外部机构报价", DT("DOUBLE"), "external_quote",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("样本数量", DT("INTEGER"), "sample_count",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("工时", DT("INTEGER"), "work_hours",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("试验仪器清单", DT("STRING"), "equipment_list",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("试验周期", DT("INTEGER"), "test_period",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("状态", DT("STRING"), "status",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_lms_trial_standard", dataset_api_name="lms_trial_standard"),
    ],
    primary_key="standardId",
    title_property="standardName",
)

DVP_DESIGN = ot(
    "DvpDesign",
    "DVP试验验证计划",
    "项目层整体试验规划，统筹全部试验任务，是整车研发周期内试验任务的顶层排程。",
    [
        prop("DVP计划编码", DT("STRING"), "dvp_code",
             table="t_dvp_design", dataset_api_name="dvp_design",
             is_primary_key=True),
        prop("所属项目令号", DT("STRING"), "project_code",
             table="t_dvp_design", dataset_api_name="dvp_design",
             description="FK → projectBase.project_code"),
        prop("计划名称", DT("STRING"), "dvp_name",
             table="t_dvp_design", dataset_api_name="dvp_design",
             is_title_property=True),
        prop("计划开始日期", DT("DATE"), "plan_start_time",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("计划结束日期", DT("DATE"), "plan_end_time",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("试验预算", DT("DOUBLE"), "test_budget",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("样车数量", DT("INTEGER"), "sample_car_count",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("资源分配", DT("STRING"), "resource_allocation",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("状态", DT("STRING"), "status",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_dvp_design", dataset_api_name="dvp_design"),
        prop("创建人", DT("STRING"), "create_by",
             table="t_dvp_design", dataset_api_name="dvp_design"),
    ],
    links=[
        # Link 20: dvpDesign → projectBase (ONE)
        link("所属项目", "ProjectBase", "ONE", "OUTGOING",
             api_name="plansFor", description="DVP 计划属于项目基准"),
        # Link 21: dvpDesign → experimentItemRound (MANY)
        link("拆分为轮次", "ExperimentItemRound", "MANY", "OUTGOING",
             api_name="splitsIntoRound", description="DVP 计划拆分为试验轮次"),
    ],
    primary_key="dvpCode",
    title_property="dvpName",
)

EXPERIMENT_ITEM_ROUND = ot(
    "ExperimentItemRound",
    "试验轮次计划",
    "试验计划的时间拆分单元，把试验任务分配到项目各开发阶段（ET/PT/SOP），绑定项目里程碑。",
    [
        prop("轮次ID", DT("STRING"), "round_id",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round",
             is_primary_key=True),
        prop("所属DVP计划编码", DT("STRING"), "dvp_code",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round",
             description="FK → dvpDesign.dvp_code"),
        prop("排程工况编码", DT("STRING"), "condition_code",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round",
             description="FK → operCondition.condition_code"),
        prop("轮次名称", DT("STRING"), "round_name",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round",
             is_title_property=True),
        prop("开发阶段", DT("STRING"), "dev_phase",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round",
             description="ET/PT/SOP 等"),
        prop("里程碑", DT("STRING"), "milestone",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
        prop("轮次开始日期", DT("DATE"), "round_start_time",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
        prop("轮次结束日期", DT("DATE"), "round_end_time",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
        prop("状态", DT("STRING"), "status",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
        prop("创建时间", DT("TIMESTAMP"), "create_time",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
        prop("更新时间", DT("TIMESTAMP"), "update_time",
             table="t_experiment_item_round", dataset_api_name="experiment_item_round"),
    ],
    links=[
        # Link 22: experimentItemRound → operCondition (MANY, schedules)
        link("排程工况", "OperCondition", "MANY", "OUTGOING",
             api_name="schedulesCondition", description="轮次排程试验工况"),
        # 链路4: experimentItemRound → testItem (MANY, schedules)
        link("排程试验项", "TestItem", "MANY", "OUTGOING",
             api_name="schedulesTestItem", description="轮次排程试验项"),
    ],
    primary_key="roundId",
    title_property="roundName",
)


# ═══════════════════════════════════════════════════════════════════════════
# Assembly: ontology payload
# ═══════════════════════════════════════════════════════════════════════════

# Order matters for readability but NOT for setup (setup creates OTs first,
# then resolves link targets by api_name). Keep domain-grouped order.
ALL_OBJECT_TYPES: list[dict] = [
    # Domain 1: Project & Target
    PROJECT_BASE, PROJECT_VEHICLE, LMS_PROJECT, PROJECT_TARGET,
    LMS_TARGET_DIMENSION, LMS_TARGET_ITERATION,
    # Domain 2: Vehicle Physical Structure
    VEHICLE_BODY, FRONT_STRUCTURE, SIDE_STRUCTURE, REAR_STRUCTURE,
    CHASSIS_STRUCTURE, EXTERIOR_DESIGN, COMPONENT, CHANGE_POINT_ENTITY,
    # Domain 3: Test & Verification
    OPER_CONDITION, FRONT_COLLISION, REAR_COLLISION, SIDE_COLLISION,
    PEDESTRIAN_PROTECT, TEST_ITEM, SPEC, LMS_TRIAL_STANDARD,
    DVP_DESIGN, EXPERIMENT_ITEM_ROUND,
]

ONTOLOGY: dict = {
    "api_name": "DVP",
    "display_name": "整车研发试验验证",
    "description": (
        "整车研发试验验证领域本体（项目→车辆→结构→零部件→变更→试验→目标），"
        "全 VIRTUAL 虚拟表经 Trino 联邦查询 MySQL，无写路径/无 AI 产物。"
    ),
    "object_types": ALL_OBJECT_TYPES,
    # DVP does NOT define action_types (no write path tested).
    "action_types": [],
}


# ── VIRTUAL datasets to register (21 physical tables → 20 datasets; the 4
# sub-conditions share one dataset ``oper_condition_detail``).
# Each entry: (dataset_api_name, mysql_table, display_name).
# Used by 01_setup_ontology.py to call register_virtual_table.
VIRTUAL_DATASETS: list[tuple[str, str, str]] = [
    ("project_base", "t_project_base", "项目基准"),
    ("project_vehicle", "t_project_vehicle", "车辆项目"),
    ("lms_project", "t_lms_project", "LMS集成项目"),
    ("project_target", "t_project_target", "项目目标"),
    ("lms_target_dimension", "t_lms_target_dimension", "LMS目标维度"),
    ("lms_target_iteration", "t_lms_target_iteration", "目标迭代"),
    ("vehicle_body", "t_vehicle_body", "车身"),
    ("front_structure", "t_front_structure", "前部结构"),
    ("side_structure", "t_side_structure", "侧面结构"),
    ("rear_structure", "t_rear_structure", "尾部结构"),
    ("chassis_structure", "t_chassis_structure", "底盘结构"),
    ("exterior_design", "t_exterior_design", "外饰造型"),
    ("component", "t_component", "零部件"),
    ("change_point_entity", "t_change_point_entity", "变化点实体"),
    ("oper_condition", "t_oper_condition", "试验工况大类"),
    # 4 sub-conditions share ONE dataset (same physical table):
    ("oper_condition_detail", "t_oper_condition_detail", "细分工况"),
    ("test_item", "t_test_item", "试验项"),
    ("spec", "t_spec", "试验规范"),
    ("lms_trial_standard", "t_lms_trial_standard", "LMS试验标准"),
    ("dvp_design", "t_dvp_design", "DVP试验验证计划"),
    ("experiment_item_round", "t_experiment_item_round", "试验轮次计划"),
]


def build() -> dict:
    """Return the full ontology payload dict."""
    return ONTOLOGY


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "data" / "ontology"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "dvp-ontology.json"
    payload = build()
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Sanity summary
    n_props = sum(len(ot["properties"]) for ot in payload["object_types"])
    n_links = sum(len(ot.get("links", [])) for ot in payload["object_types"])
    print(f"✓ Wrote {out_path}")
    print(f"  ontology: {payload['api_name']} ({payload['display_name']})")
    print(f"  object_types: {len(payload['object_types'])}")
    print(f"  properties: {n_props}")
    print(f"  links: {n_links}")
    print(f"  action_types: {len(payload['action_types'])} (DVP has none)")
    print(f"  virtual datasets: {len(VIRTUAL_DATASETS)}")


if __name__ == "__main__":
    main()
