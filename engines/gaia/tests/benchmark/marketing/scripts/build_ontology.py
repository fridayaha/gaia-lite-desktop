"""Marketing ontology generator — produces marketing-ontology.json.

This is the **single source of truth** for the Marketing benchmark ontology.
Edit the Python data structures below (not the generated JSON) to evolve the
ontology. Run::

    python -m tests.benchmark.marketing.scripts.build_ontology

to regenerate ``data/ontology/marketing-ontology.json``.

Design contract (see DESIGN.md §3):
  - ObjectTypes are PascalCase (caller-supplied).
  - Property apiNames are derived by the backend from backing_column
    (snake_case) — we only supply display_name (Chinese) + backing_mapping.
  - Identifiers (PK/FK/phone/vin/plate/code) stay ASCII; business text is
    Chinese (DESIGN.md §2.4).
  - 4 mapping fixes applied (DESIGN.md §3.2):
      1. test_drive.test_drive_consultant_id removed (+ link #38 removed).
      2. lead_follow_record columns unified to snake_case.
      3. recording is a synthetic table (seeded from 3 url sources).
      4. user simplified to single source; phone_brand/device_model have no
         backing (expected null).

Output schema aligns with ObjectTypeBatchCreate + ActionTypeCreate
(src/ontology/core/schemas/{ontology,action}.py), which the setup script
(01_setup_ontology.py) will consume.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Physical catalog/schema (the MySQL source database) ──────────────────
CATALOG = "marketing_mysql"
SCHEMA = "marketing_benchmark"

# ── Data type helper ─────────────────────────────────────────────────────
DT = str  # one of DataType StrEnum values


def prop(
    display_name: str,
    data_type: str,
    backing_column: str,
    *,
    table: str,
    description: str = "",
    is_primary_key: bool = False,
    is_title_property: bool = False,
    searchable: bool = True,
) -> dict:
    """Build a PropertyInput dict (apiName derived by backend from backing_column)."""
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
        },
    }


def prop_no_source(
    display_name: str,
    data_type: str,
    *,
    api_name: str | None = None,
    description: str = "",
    is_primary_key: bool = False,
    is_title_property: bool = False,
) -> dict:
    """Property with NO physical backing.

    Covers: fix 4 (phone_brand/device_model), AI products (ontology-created),
    and MVP master-data entities (product_capability/usp/pitch/scenario etc.
    whose displayName is Chinese and have no physical column). For these,
    ``api_name`` MUST be supplied explicitly (camelCase) — without an ASCII
    backing_column anchor, the backend would otherwise fall back to ``propertyN``.
    The caller derives it from the source ontology's English property name
    (e.g. "能力名称" → capability_name → capabilityName).
    """
    return {
        "display_name": display_name,
        "api_name": api_name,
        "description": description,
        "data_type": data_type,
        "searchable": True,
        "is_primary_key": is_primary_key or None,
        "is_title_property": is_title_property or None,
        "backing_mapping": None,
    }


def link(
    display_name: str,
    target: str,
    cardinality: str = "MANY",
    direction: str = "OUTGOING",
    *,
    api_name: str | None = None,
    description: str = "",
) -> dict:
    """Build a LinkInput dict.

    ``display_name`` is the Chinese label; ``api_name`` is the caller-supplied
    camelCase identifier (AI-assisted + user-editable per the ObjectType/Action
    pattern). The backend validates pattern + uniqueness and does NOT
    re-derive when ``api_name`` is given (so Chinese display names get a
    meaningful api_name instead of the linkTypeN fallback).
    """
    d = {
        "display_name": display_name,
        "api_name": api_name,
        "target_object_type_id": target,
        "cardinality": cardinality,
        "direction": direction,
    }
    if description:
        d["description"] = description
    return d


def obj(
    api_name: str,
    display_name: str,
    description: str,
    properties: list[dict],
    links: list[dict] | None = None,
    *,
    storage_type: str = "MANAGED",
    primary_key: str | None = None,
    title_property: str | None = None,
) -> dict:
    # Every MANAGED property's backing_mapping must carry dataset_api_name so
    # the backend back-fills PropertyDefModel.backing_dataset_api_name (the
    # ObjectType↔dataset association) at batch-create time — otherwise the
    # property is orphaned from its dataset and provision/sync can't map
    # Iceberg columns back to ontology properties. The dataset api_name is
    # the ObjectType's snake_case form (matches managed_dataset_api_name).
    from ontology.core.naming import managed_dataset_api_name

    dataset_api = managed_dataset_api_name(api_name)
    for p in properties:
        bm = p.get("backing_mapping")
        if bm is not None and not bm.get("dataset_api_name"):
            bm["dataset_api_name"] = dataset_api
    return {
        "api_name": api_name,
        "display_name": display_name,
        "description": description,
        "storage_type": storage_type,
        "primary_key": primary_key,
        "title_property": title_property,
        "properties": properties,
        "links": links or [],
    }


# ════════════════════════════════════════════════════════════════════════
# ENTITY DEFINITIONS (38 ObjectTypes, fixes applied)
# ════════════════════════════════════════════════════════════════════════

OBJECT_TYPES: list[dict] = [
    # ── 主数据 ──────────────────────────────────────────────────────────
    obj(
        "Dealership",
        "门店",
        "汽车销售门店，营销链路的组织根节点。",
        [
            prop("门店ID", "STRING", "store_code", table="t_ods_master_data_store", is_primary_key=True),
            prop("门店名称", "STRING", "org_name", table="t_ods_master_data_store", is_title_property=True),
            prop("门店类型", "STRING", "store_type", table="t_ods_master_data_store"),
            prop("建店状态", "STRING", "store_status", table="t_ods_master_data_store"),
            prop("门店描述", "STRING", "description", table="t_ods_master_data_store"),
            prop("门店大类", "STRING", "store_categories", table="t_ods_master_data_store"),
            prop("门店级别", "STRING", "store_level", table="t_ods_master_data_store"),
            prop("销售大区", "STRING", "regional_sales", table="t_ods_master_data_store"),
            prop("售后大区", "STRING", "after_sales_region", table="t_ods_master_data_store"),
            prop("省份", "STRING", "province", table="t_ods_master_data_store"),
            prop("城市", "STRING", "city", table="t_ods_master_data_store"),
            prop("区", "STRING", "area", table="t_ods_master_data_store"),
            prop("建店地址", "STRING", "address", table="t_ods_master_data_store"),
            prop("建店面积", "DECIMAL", "store_area", table="t_ods_master_data_store"),
            prop("营业开始时间", "STRING", "opening_time", table="t_ods_master_data_store"),
            prop("营业截止时间", "STRING", "business_deadline", table="t_ods_master_data_store"),
            prop("经度", "DECIMAL", "longitude", table="t_ods_master_data_store"),
            prop("纬度", "DECIMAL", "dimension", table="t_ods_master_data_store"),
            prop("是否境外", "BOOLEAN", "is_oversea", table="t_ods_master_data_store"),
            prop("国家", "STRING", "country", table="t_ods_master_data_store"),
        ],
    ),
    obj(
        "SalesConsultant",
        "销售顾问",
        "门店销售人员，承接线索分配与跟进。",
        [
            prop("销售顾问ID", "STRING", "user_id", table="t_ods_master_data_staff", is_primary_key=True),
            prop("销售顾问名称", "STRING", "user_name", table="t_ods_master_data_staff", is_title_property=True),
            prop("电话", "STRING", "phone", table="t_ods_master_data_staff"),
            prop("工号", "STRING", "job_number", table="t_ods_master_data_staff"),
            prop("是否门店管理员", "BOOLEAN", "is_store_admin", table="t_ods_master_data_staff"),
            prop("性别", "STRING", "gender", table="t_ods_master_data_staff"),
            prop("邮箱", "STRING", "email", table="t_ods_master_data_staff"),
            prop("入职时间", "TIMESTAMP", "entry_time", table="t_ods_master_data_staff"),
            prop("在职离职状态", "STRING", "leave_status", table="t_ods_master_data_staff"),
            prop("离职时间", "TIMESTAMP", "termination_time", table="t_ods_master_data_staff"),
            prop("门店编码", "STRING", "store_code", table="t_ods_master_data_staff"),
            # L6 increment-sync case needs update_time (filter) + status.
            prop("状态", "STRING", "status", table="t_ods_master_data_staff"),
            prop("创建时间", "TIMESTAMP", "create_time", table="t_ods_master_data_staff"),
            prop("更新时间", "TIMESTAMP", "update_time", table="t_ods_master_data_staff"),
        ],
        links=[link("所属门店", "Dealership", "MANY", "OUTGOING", api_name="belongsToDealership")],
    ),
    obj(
        "LeadSource",
        "线索来源渠道",
        "线索获取渠道（一级/二级/三级/四级）。",
        [
            prop("线索来源ID", "STRING", "source_id", table="t_ods_leads_server_leads_source", is_primary_key=True),
            prop("来源名称", "STRING", "show_name", table="t_ods_leads_server_leads_source", is_title_property=True),
            prop("级数", "STRING", "source_level", table="t_ods_leads_server_leads_source"),
            prop("父来源码", "STRING", "parent_source_id", table="t_ods_leads_server_leads_source"),
            prop("一级分类", "STRING", "first_classification", table="t_ods_leads_server_leads_source"),
            prop("二级分类", "STRING", "secondary_classification", table="t_ods_leads_server_leads_source"),
            prop("状态", "STRING", "status", table="t_ods_leads_server_leads_source"),
        ],
    ),
    obj(
        # Fix 4: user simplified to single source (t_ods_leads_server_leads_user_rt).
        # phone_brand / phone_device_model have NO backing (expected null).
        "User",
        "用户",
        "客户档案，营销链路的客户主体。",
        [
            prop("用户ID", "STRING", "user_id", table="t_ods_leads_server_leads_user_rt", is_primary_key=True),
            prop("客户名称", "STRING", "user_name", table="t_ods_leads_server_leads_user_rt", is_title_property=True),
            prop("联系电话", "STRING", "mobile", table="t_ods_leads_server_leads_user_rt"),
            prop("注册时间", "TIMESTAMP", "reg_time", table="t_ods_leads_server_leads_user_rt"),
            # Fix 4 (pragmatic): phone_brand / phone_device_model are real
            # columns in the source table but always NULL (no CDP data).
            prop("手机品牌", "STRING", "phone_brand", table="t_ods_leads_server_leads_user_rt"),
            prop("手机型号", "STRING", "phone_device_model", table="t_ods_leads_server_leads_user_rt"),
        ],
    ),
    # ── 线索链路 ────────────────────────────────────────────────────────
    obj(
        "Lead",
        "线索",
        "客户购车意向线索，营销链路核心实体。",
        [
            prop("线索ID", "STRING", "id", table="t_ods_leads_server_leads_info_rt", is_primary_key=True),
            prop("线索评级", "STRING", "leads_level", table="t_ods_leads_server_leads_info_rt"),
            prop("留资时间", "TIMESTAMP", "filing_time", table="t_ods_leads_server_leads_info_rt"),
            prop("四级来源", "STRING", "four_source", table="t_ods_leads_server_leads_info_rt"),
            prop("数据来源", "STRING", "data_source", table="t_ods_leads_server_leads_info_rt"),
            prop("线索渠道", "STRING", "channel", table="t_ods_leads_server_leads_info_rt"),
            prop("意向品牌", "STRING", "brand", table="t_ods_leads_server_leads_info_rt"),
            prop("意向车型", "STRING", "vehicle_model_name", table="t_ods_leads_server_leads_info_rt"),
            prop("意向车系", "STRING", "vehicle_series_name", table="t_ods_leads_server_leads_info_rt"),
            prop("客户省", "STRING", "province", table="t_ods_leads_server_leads_info_rt"),
            prop("客户市", "STRING", "city", table="t_ods_leads_server_leads_info_rt"),
            prop("客户地址", "STRING", "address", table="t_ods_leads_server_leads_info_rt"),
            prop("经销商名称", "STRING", "dealer_name", table="t_ods_leads_server_leads_info_rt"),
            prop("经销商代码", "STRING", "dealer_code", table="t_ods_leads_server_leads_info_rt"),
            prop("线索状态", "STRING", "leads_status", table="t_ods_leads_server_leads_info_rt"),
            prop("线索接收时间", "TIMESTAMP", "receive_time", table="t_ods_leads_server_leads_info_rt"),
            prop("首次下发时间", "TIMESTAMP", "first_send_time", table="t_ods_leads_server_leads_info_rt"),
            prop("首次分配时间", "TIMESTAMP", "first_assign_time", table="t_ods_leads_server_leads_info_rt"),
            prop("首次跟进时间", "TIMESTAMP", "first_follow_time", table="t_ods_leads_server_leads_info_rt"),
            prop("下次跟进时间", "TIMESTAMP", "next_follow_time", table="t_ods_leads_server_leads_info_rt"),
            # 建档时间 maps to a distinct column (留资时间 already uses filing_time).
            prop("建档时间", "TIMESTAMP", "filing_create_time", table="t_ods_leads_server_leads_info_rt"),
            prop("认领状态", "STRING", "claim_status", table="t_ods_leads_server_leads_info_rt"),
            prop("用户昵称", "STRING", "nick", table="t_ods_leads_server_leads_info_rt"),
            prop("原始门店编码", "STRING", "init_shop_code", table="t_ods_leads_server_leads_info_rt"),
            prop("线索阶段", "STRING", "stage", table="t_ods_leads_server_leads_info_rt"),
            prop("最新跟进时间", "TIMESTAMP", "last_follow_time", table="t_ods_leads_server_leads_info_rt"),
            prop("最新跟进内容", "STRING", "last_follow_content", table="t_ods_leads_server_leads_info_rt"),
            prop("是否异地线索", "BOOLEAN", "is_allopatry", table="t_ods_leads_server_leads_info_rt"),
            prop("是否试驾", "STRING", "test_drive", table="t_ods_leads_server_leads_info_rt"),
            prop("试驾状态", "STRING", "test_drive_status", table="t_ods_leads_server_leads_info_rt"),
            prop("线索标识", "STRING", "lead_mark", table="t_ods_leads_server_leads_info_rt"),
            # FK columns (also modeled as links, but exposed as queryable
            # properties so read cases can filter/join without link traversal).
            prop("用户ID", "STRING", "user_id", table="t_ods_leads_server_leads_info_rt"),
        ],
        links=[
            link("线索来源", "LeadSource", "MANY", "OUTGOING", api_name="hasLeadSource"),
            link("所属用户", "User", "MANY", "OUTGOING", api_name="belongsToUser"),
            link("关注车型", "VehicleModel", "MANY", "OUTGOING", api_name="focusesOnVehicleModel"),
            link("关注车系", "VehicleSeries", "MANY", "OUTGOING", api_name="focusesOnVehicleSeries"),
        ],
    ),
    obj(
        "LeadAllocateRecord",
        "线索分配记录",
        "线索下发/分配/转移/回收操作记录（operation_type 1-4）。",
        [
            prop("线索分配ID", "STRING", "oid", table="t_ods_source_data_leads_operation_record", is_primary_key=True),
            prop("操作时间", "TIMESTAMP", "operation_time", table="t_ods_source_data_leads_operation_record"),
            prop("操作类型", "STRING", "type", table="t_ods_source_data_leads_operation_record"),
            prop("首次标识", "STRING", "first_flag", table="t_ods_source_data_leads_operation_record"),
            prop("创建时间", "TIMESTAMP", "create_time", table="t_ods_source_data_leads_operation_record"),
            prop("更新时间", "TIMESTAMP", "update_time", table="t_ods_source_data_leads_operation_record"),
            # FK columns exposed as queryable properties.
            prop("线索ID", "STRING", "leads_id", table="t_ods_source_data_leads_operation_record"),
            prop("销售顾问ID", "STRING", "sales_consultant_id", table="t_ods_source_data_leads_operation_record"),
        ],
        links=[
            link("分配线索", "Lead", "MANY", "OUTGOING", api_name="allocatesLead"),
            link("分配销售顾问", "SalesConsultant", "MANY", "OUTGOING", api_name="allocatedToSalesConsultant"),
        ],
    ),
    obj(
        "LeadDistributeRecord",
        "线索下发记录",
        "线索下发到门店的记录。",
        [
            prop("线索下发ID", "STRING", "oid", table="t_ods_source_data_leads_operation_record", is_primary_key=True),
            prop("操作时间", "TIMESTAMP", "operation_time", table="t_ods_source_data_leads_operation_record"),
            prop("操作类型", "STRING", "type", table="t_ods_source_data_leads_operation_record"),
            prop("首次标识", "STRING", "first_flag", table="t_ods_source_data_leads_operation_record"),
            prop("创建时间", "TIMESTAMP", "create_time", table="t_ods_source_data_leads_operation_record"),
        ],
        links=[
            link("下发线索", "Lead", "MANY", "OUTGOING", api_name="distributesLead"),
            link("下发门店", "Dealership", "MANY", "OUTGOING", api_name="distributedToDealership"),
        ],
    ),
    obj(
        # Fix 2: all columns unified to snake_case.
        "LeadFollowRecord",
        "线索跟进记录",
        "销售对线索的跟进记录。",
        [
            prop("跟进ID", "STRING", "oid", table="t_ods_source_data_leads_follow_record", is_primary_key=True),
            prop("跟进目的", "STRING", "follow_purpose", table="t_ods_source_data_leads_follow_record"),
            prop("沟通方式", "STRING", "communication_methods", table="t_ods_source_data_leads_follow_record"),
            prop("跟进结果", "STRING", "follow_result", table="t_ods_source_data_leads_follow_record"),
            prop("跟进内容", "STRING", "follow_content", table="t_ods_source_data_leads_follow_record"),
            prop("意向级别", "STRING", "intended_level", table="t_ods_source_data_leads_follow_record"),
            prop("意向车型编码", "STRING", "vehicle_model_code", table="t_ods_source_data_leads_follow_record"),
            prop("意向车型", "STRING", "vehicle_model_name", table="t_ods_source_data_leads_follow_record"),
            prop("订单号", "STRING", "business_no", table="t_ods_source_data_leads_follow_record"),
            prop("下次跟进时间", "TIMESTAMP", "next_follow_time", table="t_ods_source_data_leads_follow_record"),
            prop("跟进门店编码", "STRING", "follow_shop_id", table="t_ods_source_data_leads_follow_record"),
            prop("到店时间", "TIMESTAMP", "arrive_time", table="t_ods_source_data_leads_follow_record"),
            prop("战败类型", "STRING", "defeat_type", table="t_ods_source_data_leads_follow_record"),
            prop("是否转商机", "STRING", "change_business_opportunity", table="t_ods_source_data_leads_follow_record"),
            prop("创建时间", "TIMESTAMP", "create_time", table="t_ods_source_data_leads_follow_record"),
        ],
        links=[
            link("跟进线索", "Lead", "MANY", "OUTGOING", api_name="followsUpLead"),
            link("跟进销售顾问", "SalesConsultant", "MANY", "OUTGOING", api_name="followedBySalesConsultant"),
            link("跟进门店", "Dealership", "MANY", "OUTGOING", api_name="followedAtDealership"),
        ],
    ),
    # ── 外呼 ────────────────────────────────────────────────────────────
    obj(
        "ManualOutboundCall",
        "人工外呼",
        "销售人工外呼记录。",
        [
            prop("人工外呼ID", "STRING", "id", table="t_ods_leads_server_sale_call_record_rt", is_primary_key=True),
            prop("呼叫状态", "STRING", "call_status", table="t_ods_leads_server_sale_call_record_rt"),
            prop("呼叫时间", "TIMESTAMP", "call_time", table="t_ods_leads_server_sale_call_record_rt"),
            prop("呼叫时长", "INTEGER", "call_duration", table="t_ods_leads_server_sale_call_record_rt"),
            # FK columns exposed as queryable properties.
            prop("线索ID", "STRING", "lead_id", table="t_ods_leads_server_sale_call_record_rt"),
            prop("用户ID", "STRING", "user_id", table="t_ods_leads_server_sale_call_record_rt"),
            prop("录音ID", "STRING", "original_record_url", table="t_ods_leads_server_sale_call_record_rt"),
        ],
        links=[
            link("外呼线索", "Lead", "MANY", "OUTGOING", api_name="outboundCallForLead"),
            link("外呼用户", "User", "MANY", "OUTGOING", api_name="outboundCallToUser"),
            link("外呼录音", "Recording", "ONE", "OUTGOING", api_name="hasOutboundCallRecording"),
        ],
    ),
    obj(
        "AiOutboundCall",
        "AI外呼",
        "AI 机器人外呼记录。",
        [
            prop("AI外呼ID", "STRING", "id", table="t_ods_leads_server_ai_call_out_result_rt", is_primary_key=True),
            prop("AI外呼标签", "STRING", "ai_tag_name", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("AI外呼任务", "STRING", "task_name", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("呼叫时长", "INTEGER", "call_duration_sec", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("呼叫状态", "STRING", "call_status", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("呼叫时间", "TIMESTAMP", "call_times", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("开始时间", "TIMESTAMP", "call_start_time", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("结束时间", "TIMESTAMP", "call_end_time", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("是否评审", "STRING", "is_review", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("客户姓名", "STRING", "customer_name", table="t_ods_leads_server_ai_call_out_result_rt"),
            prop("电话", "STRING", "cellphone", table="t_ods_leads_server_ai_call_out_result_rt"),
            # FK column exposed as queryable property.
            prop("线索ID", "STRING", "leads_info_id", table="t_ods_leads_server_ai_call_out_result_rt"),
        ],
        links=[
            link("AI外呼线索", "Lead", "MANY", "OUTGOING", api_name="aiOutboundCallForLead"),
            link("AI外呼录音", "Recording", "ONE", "OUTGOING", api_name="hasAiOutboundCallRecording"),
        ],
    ),
    obj(
        # Fix 3: recording is a SYNTHETIC table seeded from 3 url sources.
        "Recording",
        "录音",
        "录音记录（合成实体，由试驾/外呼录音 url 归并生成）。",
        [
            prop("录音ID", "STRING", "recording_id", table="recording", is_primary_key=True),
            prop("录音URL", "STRING", "recording_url", table="recording", is_title_property=True),
            prop("录音文本", "STRING", "recording_text", table="recording"),
        ],
    ),
    # ── 试驾 ────────────────────────────────────────────────────────────
    obj(
        # Fix 1: test_drive_consultant_id REMOVED (physical column does not exist;
        # source field was a typo pointing at the PK test_drive_id). Only
        # sales_consultant_id (→ sale_id) remains.
        "TestDrive",
        "试驾",
        "客户试驾记录，含排程、状态机、录音关联。",
        [
            prop("试驾ID", "STRING", "id", table="t_ods_test_drive_test_drive_rt", is_primary_key=True),
            prop("结束时间", "TIMESTAMP", "end_time", table="t_ods_test_drive_test_drive_rt"),
            prop("开始时间", "TIMESTAMP", "begin_time", table="t_ods_test_drive_test_drive_rt"),
            prop("用户名", "STRING", "name", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾人手机", "STRING", "phone", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾类型", "STRING", "test_drive_type", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾门店编码", "STRING", "store_code", table="t_ods_test_drive_test_drive_rt"),
            prop("排程时间", "TIMESTAMP", "schedule_time", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾日期", "TIMESTAMP", "test_drive_date", table="t_ods_test_drive_test_drive_rt"),
            prop("上门时间", "TIMESTAMP", "door_time", table="t_ods_test_drive_test_drive_rt"),
            prop("上门地址", "STRING", "door_address", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾时长", "INTEGER", "duration", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾公里数", "DECIMAL", "kilometre", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾单状态", "STRING", "order_status", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾类别", "STRING", "test_drive_class", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾来源", "STRING", "test_drive_source", table="t_ods_test_drive_test_drive_rt"),
            prop("预约车系", "STRING", "intended_car_series", table="t_ods_test_drive_test_drive_rt"),
            prop("更新时间", "TIMESTAMP", "update_time", table="t_ods_test_drive_test_drive_rt"),
            # FK columns exposed as queryable properties (Fix 1: sales_consultant
            # via sale_id only; no test_drive_consultant_id).
            prop("销售顾问ID", "STRING", "sale_id", table="t_ods_test_drive_test_drive_rt"),
            prop("用户ID", "STRING", "user_id", table="t_ods_test_drive_test_drive_rt"),
            prop("线索ID", "STRING", "leads_id", table="t_ods_test_drive_test_drive_rt"),
            prop("试驾车辆ID", "STRING", "test_drive_car_id", table="t_ods_test_drive_test_drive_rt"),
            prop("录音ID", "STRING", "original_record_url", table="t_ods_test_drive_test_drive_rt"),
        ],
        links=[
            link("试驾销售顾问", "SalesConsultant", "MANY", "OUTGOING", api_name="testDriveSalesConsultant"),
            link("试驾录音", "Recording", "ONE", "OUTGOING", api_name="hasTestDriveRecording"),
            link("试驾用户", "User", "MANY", "OUTGOING", api_name="testDriveUser"),
            link("试驾线索", "Lead", "MANY", "OUTGOING", api_name="testDriveLead"),
            link("试驾路线", "TestDriveRoute", "MANY", "OUTGOING", api_name="usesTestDriveRoute"),
            link("试驾车辆", "TestDriveCar", "MANY", "OUTGOING", api_name="usesTestDriveCar"),
            link("试驾门店", "Dealership", "MANY", "OUTGOING", api_name="testDriveAtDealership"),
        ],
    ),
    obj(
        "TestDriveCar",
        "试驾车",
        "门店试驾车辆。",
        [
            prop("主键ID", "STRING", "id", table="t_ods_test_drive_car_model", is_primary_key=True),
            prop("VIN码", "STRING", "car_model_vin", table="t_ods_test_drive_car_model"),
            prop("门店编码", "STRING", "store_code", table="t_ods_test_drive_car_model"),
            prop(
                "试驾车型名称", "STRING", "car_model_name", table="t_ods_test_drive_car_model", is_title_property=True
            ),
            prop("车牌号", "STRING", "number_plate", table="t_ods_test_drive_car_model"),
            prop("车系名称", "STRING", "car_series_name", table="t_ods_test_drive_car_model"),
            prop("车型名称", "STRING", "model_name", table="t_ods_test_drive_car_model"),
            prop("启用状态", "STRING", "car_status", table="t_ods_test_drive_car_model"),
            prop("状态", "STRING", "status", table="t_ods_test_drive_car_model"),
        ],
        links=[
            link("所属门店", "Dealership", "MANY", "OUTGOING", api_name="carBelongsToDealership"),
            link("车辆车型", "VehicleModel", "MANY", "OUTGOING", api_name="carVehicleModel"),
            link("车辆车系", "VehicleSeries", "MANY", "OUTGOING", api_name="carVehicleSeries"),
        ],
    ),
    obj(
        "TestDriveRoute",
        "试驾路线",
        "门店试驾路线。",
        [
            prop("试驾路线ID", "STRING", "id", table="t_ods_test_drive_route", is_primary_key=True),
            prop("路线名称", "STRING", "route_name", table="t_ods_test_drive_route", is_title_property=True),
            prop("门店编码", "STRING", "store_code", table="t_ods_test_drive_route"),
            prop("是否启用", "STRING", "is_enable", table="t_ods_test_drive_route"),
            prop("状态", "STRING", "status", table="t_ods_test_drive_route"),
        ],
        links=[link("路线门店", "Dealership", "MANY", "OUTGOING", api_name="routeDealership")],
    ),
    # ── 试驾报告（AI 产物，5 表，ontology-created, no backing） ─────────
    obj(
        "TdAnalysisDetails",
        "试驾报告-分析维度明细",
        "AI 试驾报告分析维度明细表。",
        [
            prop_no_source("分析明细ID", "STRING", api_name="tdAnalysisDetailsId", is_primary_key=True),
            prop_no_source("分析项类型", "STRING", api_name="itemType"),
            prop_no_source("维度键", "STRING", api_name="dimKey"),
            prop_no_source("得分", "INTEGER", api_name="score"),
            prop_no_source("情绪值", "DECIMAL", api_name="sentimentScore"),
            prop_no_source("AI生成摘要", "STRING", api_name="summary", is_title_property=True),
            prop_no_source("AI分析置信度", "DECIMAL", api_name="confidenceScore"),
            prop_no_source("是否经过人工校核", "STRING", api_name="isVerified"),
        ],
        links=[link("关联试驾", "TestDrive", "MANY", "OUTGOING", api_name="tdAnalysisAssociatedTestDrive")],
    ),
    obj(
        "CompetitiveAnalysis",
        "试驾报告-竞品对比分析",
        "AI 试驾报告竞品对比分析表。",
        [
            prop_no_source("竞品对比分析ID", "STRING", api_name="competitiveAnalysisId", is_primary_key=True),
            prop_no_source("对比维度", "STRING", api_name="comparisonPoint"),
            prop_no_source("对比维度得分描述", "STRING", api_name="comparisonDimension"),
            prop_no_source("客户偏好", "STRING", api_name="customerPreference"),
            prop_no_source("客户评价提炼", "STRING", api_name="comment", is_title_property=True),
        ],
        links=[
            link("关联试驾", "TestDrive", "MANY", "OUTGOING", api_name="competitiveAssociatedTestDrive"),
            link("关联竞品", "Competitor", "MANY", "OUTGOING", api_name="associatedCompetitor"),
        ],
    ),
    obj(
        "StrategyExecutionAudit",
        "试驾报告-销售策略执行审计",
        "AI 试驾报告销售策略执行审计表。",
        [
            prop_no_source("审计ID", "STRING", api_name="strategyExecutionAuditId", is_primary_key=True),
            prop_no_source("检查项标识", "STRING", api_name="auditItemKey"),
            prop_no_source("检查项名称", "STRING", api_name="itemName", is_title_property=True),
            prop_no_source("策略分类", "STRING", api_name="category"),
            prop_no_source("执行状态", "STRING", api_name="status"),
            prop_no_source("权重分值", "DECIMAL", api_name="scoreImpact"),
            prop_no_source("执行表现摘要", "STRING", api_name="summary"),
        ],
        links=[link("关联试驾", "TestDrive", "MANY", "OUTGOING", api_name="strategyAuditAssociatedTestDrive")],
    ),
    obj(
        "ScriptExecutionAnalysis",
        "试驾报告-话术执行分析",
        "AI 试驾报告话术执行深度分析表。",
        [
            prop_no_source("话术执行分析ID", "STRING", api_name="scriptExecutionAnalysisId", is_primary_key=True),
            prop_no_source("话术模块名称", "STRING", api_name="scriptModule", is_title_property=True),
            prop_no_source("话术分类", "STRING", api_name="scriptCategory"),
            prop_no_source("执行质量得分", "INTEGER", api_name="executionQuality"),
            prop_no_source("客户是否积极反馈", "STRING", api_name="customerResonance"),
            prop_no_source("话术优化建议", "STRING", api_name="optimizationAdvice"),
        ],
        links=[link("关联试驾", "TestDrive", "MANY", "OUTGOING", api_name="scriptAnalysisAssociatedTestDrive")],
    ),
    obj(
        "FocusResistancePoints",
        "试驾报告-关注点抗性点",
        "AI 试驾报告关注点/抗性点明细表。",
        [
            prop_no_source("关注点抗性点ID", "STRING", api_name="focusResistancePointsId", is_primary_key=True),
            prop_no_source("功能点名称", "STRING", api_name="featureName", is_title_property=True),
            prop_no_source("点类型", "STRING", api_name="pointType"),
            prop_no_source("情感分值", "DECIMAL", api_name="sentimentScore"),
            prop_no_source("影响权重", "DECIMAL", api_name="weight"),
            prop_no_source("是否已化解", "STRING", api_name="isResolved"),
        ],
        links=[link("关联试驾", "TestDrive", "MANY", "OUTGOING", api_name="focusResistanceAssociatedTestDrive")],
    ),
    # ── 用户画像（AI 产物，8 表，ontology-created, no backing） ─────────
    obj(
        "UserProfileBasicNote",
        "用户画像-基础属性",
        "AI 用户画像基础属性表。",
        [
            prop_no_source("属性名", "STRING", api_name="attributeKey", is_primary_key=True),
            prop_no_source("分类", "STRING", api_name="category"),
            prop_no_source("属性值", "STRING", api_name="attributeValue", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="basicNoteProfileUser")],
    ),
    obj(
        "UserProfileOverview",
        "用户画像-overview",
        "AI 用户画像概览表。",
        [
            prop_no_source("属性名", "STRING", api_name="attributeKey", is_primary_key=True),
            prop_no_source("属性值", "STRING", api_name="attributeValue", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="overviewProfileUser")],
    ),
    obj(
        "CustomerProfileEmotion",
        "用户画像-情绪",
        "AI 用户画像情绪表（按角色分级可见，安全 S5）。",
        [
            prop_no_source("属性名", "STRING", api_name="attributeKey", is_primary_key=True),
            prop_no_source("属性值", "STRING", api_name="attributeValue", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="emotionProfileUser")],
    ),
    obj(
        "CustomerProfileInferredTag",
        "用户画像-推断标签",
        "AI 用户画像推断标签表。",
        [
            prop_no_source("标签", "STRING", api_name="title", is_primary_key=True),
            prop_no_source("描述", "STRING", api_name="description", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="inferredTagProfileUser")],
    ),
    obj(
        "CustomerProfileUsageScenario",
        "用户画像-用车场景",
        "AI 用户画像用车场景表。",
        [
            prop_no_source("用车场景", "STRING", api_name="title", is_primary_key=True),
            prop_no_source("描述", "STRING", api_name="description", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="usageScenarioProfileUser")],
    ),
    obj(
        "CustomerProfilePurchaseMotivation",
        "用户画像-购车动机",
        "AI 用户画像购车动机表。",
        [
            prop_no_source("购车动机", "STRING", api_name="motivationName", is_primary_key=True),
            prop_no_source("描述", "STRING", api_name="description", is_title_property=True),
            prop_no_source("排序", "INTEGER", api_name="rankOrder"),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="purchaseMotivationProfileUser")],
    ),
    obj(
        "CustomerProfileProductPreference",
        "用户画像-产品偏好",
        "AI 用户画像产品偏好表。",
        [
            prop_no_source("偏好", "STRING", api_name="preferenceName", is_primary_key=True),
            prop_no_source("描述", "STRING", api_name="description", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="productPreferenceProfileUser")],
    ),
    obj(
        "CustomerProfileResistance",
        "用户画像-抗性分析",
        "AI 用户画像抗性分析表（按角色分级可见，安全 S5）。",
        [
            prop_no_source("抗性名称", "STRING", api_name="resistanceName", is_primary_key=True),
            prop_no_source("严重程度", "STRING", api_name="severity"),
            prop_no_source("描述", "STRING", api_name="description", is_title_property=True),
            prop_no_source("置信度", "DECIMAL", api_name="confidence"),
            prop_no_source("推理细节", "STRING", api_name="reasoningDetail"),
        ],
        links=[link("画像用户", "User", "MANY", "OUTGOING", api_name="resistanceProfileUser")],
    ),
    # ── 产品知识 ────────────────────────────────────────────────────────
    obj(
        "VehicleSeries",
        "车系",
        "车系主数据。",
        [
            prop_no_source("车系名称", "STRING", api_name="seriesName", is_primary_key=True, is_title_property=True),
            prop_no_source("品牌", "STRING", api_name="brand"),
            prop_no_source("细分市场", "STRING", api_name="segment"),
            prop_no_source("能源类型", "STRING", api_name="energyType"),
            prop_no_source("生命周期阶段", "STRING", api_name="lifecycleStage"),
        ],
    ),
    obj(
        "VehicleModel",
        "车型版本",
        "车型版本主数据。",
        [
            prop_no_source("车型名称", "STRING", api_name="modelName", is_primary_key=True, is_title_property=True),
            prop_no_source("指导价格", "DECIMAL", api_name="guidedPrice"),
            prop_no_source("车辆可获得性", "STRING", api_name="vehicleAvailability"),
            prop_no_source("交付周期", "INTEGER", api_name="deliveryCycleDays"),
            prop_no_source("利润水平", "STRING", api_name="marginLevel"),
            prop_no_source("系统总功率", "DECIMAL", api_name="totalSystemPowerKw"),
            prop_no_source("系统总扭矩", "DECIMAL", api_name="totalSystemTorqueNm"),
            prop_no_source("纯电续航", "DECIMAL", api_name="cltcElectricRangeKm"),
            prop_no_source("零百加速", "DECIMAL", api_name="zeroTo100kmhAcceleration"),
        ],
        links=[
            link("所属车系", "VehicleSeries", "ONE", "OUTGOING", api_name="belongsToVehicleSeries"),
            link("包含配置项", "ConfigFeature", "ONE", "OUTGOING", api_name="includesConfigFeature"),
        ],
    ),
    obj(
        "ConfigFeature",
        "配置项",
        "车辆配置项主数据。",
        [
            prop_no_source("配置名称", "STRING", api_name="configName", is_primary_key=True, is_title_property=True),
            prop_no_source("分类", "STRING", api_name="category"),
            prop_no_source("是否标配", "STRING", api_name="isStandard"),
            prop_no_source("感知价值", "STRING", api_name="perceivedValue"),
            prop_no_source("描述", "STRING", api_name="description"),
        ],
        links=[link("支撑产品能力", "ProductCapability", "MANY", "OUTGOING", api_name="supportsProductCapability")],
    ),
    obj(
        "ProductCapability",
        "产品能力",
        "产品能力主数据。",
        [
            prop_no_source(
                "能力名称", "STRING", api_name="capabilityName", is_primary_key=True, is_title_property=True
            ),
            prop_no_source("能力类型", "STRING", api_name="capabilityType"),
            prop_no_source("行业水平", "STRING", api_name="industryLevel"),
            prop_no_source("可感知性", "STRING", api_name="perceptibility"),
        ],
        links=[
            link("衡量指标", "CapabilityMetric", "ONE", "OUTGOING", api_name="measuredByCapabilityMetric"),
            link("呈现为核心卖点", "Usp", "MANY", "OUTGOING", api_name="expressedAsUsp"),
        ],
    ),
    obj(
        "CapabilityMetric",
        "能力指标",
        "产品能力衡量指标。",
        [
            prop_no_source("指标名称", "STRING", api_name="metricName", is_primary_key=True, is_title_property=True),
            prop_no_source("指标值", "DECIMAL", api_name="metricValue"),
            prop_no_source("单位", "STRING", api_name="unit"),
            prop_no_source("基准值", "DECIMAL", api_name="benchmark"),
        ],
    ),
    obj(
        "Usp",
        "核心卖点",
        "核心卖点主数据。",
        [
            prop_no_source("卖点名称", "STRING", api_name="uspName", is_primary_key=True, is_title_property=True),
            prop_no_source("卖点分类", "STRING", api_name="uspCategory"),
            prop_no_source("差异化优势", "STRING", api_name="differentiation"),
            prop_no_source("可信度", "STRING", api_name="credibility"),
            prop_no_source("传播成本", "STRING", api_name="costOfCommunication"),
        ],
        links=[
            link("关联标签", "UspTag", "ONE", "OUTGOING", api_name="taggedByUspTag"),
            link("对应话术", "Pitch", "ONE", "OUTGOING", api_name="verbalizedAsPitch"),
        ],
    ),
    obj(
        "UspTag",
        "卖点标签",
        "卖点标签。",
        [
            prop_no_source("标签名称", "STRING", api_name="tagName", is_primary_key=True, is_title_property=True),
            prop_no_source("标签类型", "STRING", api_name="tagType"),
        ],
    ),
    obj(
        "Pitch",
        "话术",
        "销售话术。",
        [
            prop_no_source("话术名称", "STRING", api_name="pitchName", is_primary_key=True, is_title_property=True),
            prop_no_source("话术文本", "STRING", api_name="pitchText"),
            prop_no_source("话语音调", "STRING", api_name="pitchTone"),
            prop_no_source("复杂度", "STRING", api_name="complexity"),
            prop_no_source("风险等级", "STRING", api_name="riskLevel"),
        ],
        links=[link("适用场景", "Scenario", "MANY", "OUTGOING", api_name="usedForScenario")],
    ),
    obj(
        "Scenario",
        "使用场景",
        "车辆使用场景。",
        [
            prop_no_source("场景名称", "STRING", api_name="scenarioName", is_primary_key=True, is_title_property=True),
            prop_no_source("使用频率", "STRING", api_name="frequency"),
            prop_no_source("情感特质", "STRING", api_name="emotionalTrait"),
        ],
    ),
    # ── 其他 ────────────────────────────────────────────────────────────
    obj(
        "Reception",
        "接待",
        "客户到店接待记录。",
        [
            prop_no_source("接待ID", "STRING", api_name="receptionId", is_primary_key=True),
            prop_no_source("开始时间", "TIMESTAMP", api_name="startTime"),
            prop_no_source("结束时间", "TIMESTAMP", api_name="endTime"),
        ],
        links=[
            link("接待用户", "User", "MANY", "OUTGOING", api_name="receptionUser"),
            link("接待销售顾问", "SalesConsultant", "MANY", "OUTGOING", api_name="receptionSalesConsultant"),
        ],
    ),
    obj(
        "ChatRecord",
        "微信聊天记录",
        "客户微信聊天记录。",
        [
            prop("聊天ID", "STRING", "id", table="t_ods_inspection_weixin_log", is_primary_key=True),
            prop("记录类型", "STRING", "record_type", table="t_ods_inspection_weixin_log"),
            prop("聊天内容", "STRING", "dialoguecontent", table="t_ods_inspection_weixin_log", is_title_property=True),
            prop("聊天创建时间", "TIMESTAMP", "createtime", table="t_ods_inspection_weixin_log"),
            prop("状态", "STRING", "status", table="t_ods_inspection_weixin_log"),
            prop("记录时间", "TIMESTAMP", "log_time", table="t_ods_inspection_weixin_log"),
        ],
        links=[link("聊天用户", "User", "MANY", "OUTGOING", api_name="chatRecordUser")],
    ),
    obj(
        "Competitor",
        "竞品",
        "竞品车型（被竞品对比分析引用）。",
        [
            prop_no_source("竞品ID", "STRING", api_name="competitorId", is_primary_key=True, is_title_property=True),
        ],
    ),
]


# ════════════════════════════════════════════════════════════════════════
# ACTION DEFINITIONS (Write path W1-W11)
# api_name is camelCase (caller-supplied). affected_object_type = PascalCase.
# ════════════════════════════════════════════════════════════════════════


def _param(
    api_name: str,
    display_name: str,
    data_type: str,
    *,
    required: bool = True,
    description: str = "",
    object_type_ref: str | None = None,
) -> dict:
    p = {
        "api_name": api_name,
        "display_name": display_name,
        "data_type": data_type,
        "required": required,
        "description": description,
    }
    if object_type_ref:
        p["object_type_ref"] = object_type_ref
    return p


# ValueSource helpers (align with ontology.core.schemas.action.ValueSource:
# {source: PARAMETER|STATIC_VALUE|SYSTEM_CONTEXT|SYSTEM_GENERATED|EXPRESSION|OBJECT_PROPERTY, value: str})
def _vs_param(name: str) -> dict:
    return {"source": "PARAMETER", "value": name}


def _vs_static(value: str) -> dict:
    return {"source": "STATIC_VALUE", "value": value}


def _vs_now() -> dict:
    return {"source": "SYSTEM_CONTEXT", "value": "CURRENT_TIMESTAMP"}


def _vs_uuid() -> dict:
    return {"source": "SYSTEM_GENERATED", "value": "uuid"}


def _action(
    api_name: str,
    display_name: str,
    description: str,
    affected: str,
    parameters: list[dict],
    *,
    rules: list[dict] | None = None,
    ontology_rules: list[dict] | None = None,
    effects: list[dict] | None = None,
    operation_kind: str = "mixed",
) -> dict:
    return {
        "api_name": api_name,
        "display_name": display_name,
        "description": description,
        "affected_object_type_api_name": affected,
        "parameters": parameters,
        "rules": rules or [],
        "submission_criteria": [],
        "effects": effects or [],
        "ontology_rules": ontology_rules or [],
        "risk_level": "low",
        "operation_kind": operation_kind,
        "batch_enabled": False,
    }


ACTIONS: list[dict] = [
    # W1/W2/W3: 线索分配/转移/回收 — operate on lead_allocate_record + lead
    _action(
        "allocateLead",
        "分配线索",
        "将线索分配给销售顾问（operation_type=2）。",
        "LeadAllocateRecord",
        [
            _param("leadId", "线索ID", "STRING", object_type_ref="Lead"),
            _param("salesConsultantId", "销售顾问ID", "STRING", object_type_ref="SalesConsultant"),
        ],
        ontology_rules=[
            {
                "type": "CreateObject",
                "target_object_type": "LeadAllocateRecord",
                "properties": {
                    "oid": _vs_uuid(),
                    "operationType": _vs_static("2"),
                    "operationTime": _vs_now(),
                    "leadId": _vs_param("leadId"),
                    "salesConsultantId": _vs_param("salesConsultantId"),
                },
            },
        ],
        operation_kind="create",
    ),
    _action(
        "transferLead",
        "转移线索",
        "将线索从一销售转移给另一销售（operation_type=3）。",
        "LeadAllocateRecord",
        [
            _param("leadId", "线索ID", "STRING", object_type_ref="Lead"),
            _param("fromSalesConsultantId", "原销售顾问ID", "STRING", object_type_ref="SalesConsultant"),
            _param("toSalesConsultantId", "新销售顾问ID", "STRING", object_type_ref="SalesConsultant"),
        ],
        ontology_rules=[
            {
                "type": "CreateObject",
                "target_object_type": "LeadAllocateRecord",
                "properties": {
                    "oid": _vs_uuid(),
                    "operationType": _vs_static("3"),
                    "operationTime": _vs_now(),
                    "leadId": _vs_param("leadId"),
                    "salesConsultantId": _vs_param("toSalesConsultantId"),
                },
            },
        ],
        operation_kind="create",
    ),
    _action(
        "reclaimLead",
        "回收线索",
        "回收线索（operation_type=4）。",
        "LeadAllocateRecord",
        [
            _param("leadId", "线索ID", "STRING", object_type_ref="Lead"),
        ],
        ontology_rules=[
            {
                "type": "CreateObject",
                "target_object_type": "LeadAllocateRecord",
                "properties": {
                    "oid": _vs_uuid(),
                    "operationType": _vs_static("4"),
                    "operationTime": _vs_now(),
                    "leadId": _vs_param("leadId"),
                },
            },
        ],
        operation_kind="create",
    ),
    # W4: 线索跟进记录写入
    _action(
        "recordFollow",
        "记录线索跟进",
        "销售记录一次线索跟进。",
        "LeadFollowRecord",
        [
            _param("leadId", "线索ID", "STRING", object_type_ref="Lead"),
            _param("followerId", "跟进人ID", "STRING", object_type_ref="SalesConsultant"),
            _param("followPurpose", "跟进目的", "STRING", required=False),
            _param("followResult", "跟进结果", "STRING", required=False),
            _param("followContent", "跟进内容", "STRING", required=False),
            _param("nextFollowTime", "下次跟进时间", "TIMESTAMP", required=False),
        ],
        ontology_rules=[
            {
                "type": "CreateObject",
                "target_object_type": "LeadFollowRecord",
                "properties": {
                    "oid": _vs_uuid(),
                    "leadId": _vs_param("leadId"),
                    "followerId": _vs_param("followerId"),
                    "followPurpose": _vs_param("followPurpose"),
                    "followResult": _vs_param("followResult"),
                    "followContent": _vs_param("followContent"),
                    "nextFollowTime": _vs_param("nextFollowTime"),
                    "createTime": _vs_now(),
                },
            },
        ],
        operation_kind="create",
    ),
    # W5: 试驾状态流转 (order_status 0→1→2→3→4)
    _action(
        "progressTestDrive",
        "推进试驾状态",
        "推进试驾单状态（待排程→待签署→待开始→进行中→已结束）。",
        "TestDrive",
        [
            _param("testDriveId", "试驾ID", "STRING", object_type_ref="TestDrive"),
            _param("newStatus", "新状态", "STRING", description="0-待排程 1-待签署 2-待开始 3-进行中 4-已结束"),
        ],
        rules=[
            {
                "type": "constraint",
                "target": "newStatus",
                "expression": "value in ['0','1','2','3','4']",
                "description": "状态值必须合法",
            },
        ],
        ontology_rules=[
            {
                "type": "ModifyObject",
                "target_parameter": "testDriveId",
                "properties": {
                    "orderStatus": _vs_param("newStatus"),
                },
            },
        ],
        effects=[
            {"type": "write_back", "config": {"target_object_type": "TestDrive", "op": "upsert"}},
        ],
        operation_kind="update",
    ),
    # W6: 外呼记录写入
    _action(
        "logManualCall",
        "记录人工外呼",
        "记录一次人工外呼。",
        "ManualOutboundCall",
        [
            _param("leadId", "线索ID", "STRING", object_type_ref="Lead"),
            _param("userId", "用户ID", "STRING", object_type_ref="User"),
            _param("callStatus", "呼叫状态", "STRING"),
            _param("callDuration", "呼叫时长", "INTEGER", required=False),
            _param("recordingUrl", "录音URL", "STRING", required=False),
        ],
        ontology_rules=[
            {
                "type": "CreateObject",
                "target_object_type": "ManualOutboundCall",
                "properties": {
                    "id": _vs_uuid(),
                    "leadId": _vs_param("leadId"),
                    "userId": _vs_param("userId"),
                    "callStatus": _vs_param("callStatus"),
                    "callDuration": _vs_param("callDuration"),
                    "callTime": _vs_now(),
                },
            },
        ],
        operation_kind="create",
    ),
    # W7: AI 产物 — 试驾报告 5 表
    _action(
        "analyzeTestDrive",
        "生成试驾报告",
        "试驾完成后触发 AI 推导，生成试驾报告 5 张表（分析维度/竞品对比/策略审计/话术执行/关注点抗性点）。",
        "TdAnalysisDetails",
        [
            _param("testDriveId", "试驾ID", "STRING", object_type_ref="TestDrive"),
        ],
        operation_kind="create",
    ),
    # W8: AI 产物 — 用户画像 8 表
    _action(
        "generateUserProfile",
        "生成用户画像",
        "为指定用户触发 AI 推导，生成 8 张用户画像表（基础属性/概览/情绪/标签/用车场景/购车动机/产品偏好/抗性）。",
        "UserProfileBasicNote",
        [
            _param("userId", "用户ID", "STRING", object_type_ref="User"),
        ],
        operation_kind="create",
    ),
    # W11: Action 规则类型转换（回归缺陷#3：ObjectReference 参数比较）
    _action(
        "reassignTestDriveCar",
        "更换试驾车",
        "为试驾更换试驾车（含 ObjectReference 参数比较的规则）。",
        "TestDrive",
        [
            _param("testDriveId", "试驾ID", "STRING", object_type_ref="TestDrive"),
            _param("newTestDriveCarId", "新试驾车ID", "STRING", object_type_ref="TestDriveCar"),
        ],
        rules=[
            {
                "type": "constraint",
                "target": "newTestDriveCarId",
                "expression": "value != '' and len(value) > 0",
                "description": "试驾车ID非空",
            },
        ],
        ontology_rules=[
            {
                "type": "ModifyObject",
                "target_parameter": "testDriveId",
                "properties": {
                    "testDriveCarId": _vs_param("newTestDriveCarId"),
                },
            },
        ],
        operation_kind="update",
    ),
]


# ════════════════════════════════════════════════════════════════════════
# ONTOLOGY ROOT
# ════════════════════════════════════════════════════════════════════════

ONTOLOGY = {
    "api_name": "Marketing",  # PascalCase (handoff §四-1)
    "display_name": "汽车门店营销",
    "description": "汽车门店营销链路本体（线索→跟进→试驾→外呼→成交），含 AI 试驾报告与用户画像产物。",
    "object_types": OBJECT_TYPES,
    "action_types": ACTIONS,
}


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "ontology" / "marketing-ontology.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ONTOLOGY, ensure_ascii=False, indent=2), encoding="utf-8")
    # Stats
    n_ot = len(OBJECT_TYPES)
    n_props = sum(len(ot["properties"]) for ot in OBJECT_TYPES)
    n_links = sum(len(ot.get("links", [])) for ot in OBJECT_TYPES)
    print(f"✓ Wrote {out}")
    print(f"  ObjectTypes: {n_ot}")
    print(f"  Properties:  {n_props}")
    print(f"  Links:       {n_links}")
    print(f"  Actions:     {len(ACTIONS)}")


if __name__ == "__main__":
    main()
