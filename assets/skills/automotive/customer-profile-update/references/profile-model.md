# 客户画像数据模型

画像查询接口（`GET /api/v1/remote/data/profile/{phone}`）返回 7 大模块的 AI 分析结果。本文件详述各模块字段。

> **注意**：`basic_notes` 的枚举映射（如 `replacement`→"换购"）、JSON 字符串字段解析（如 `driver_license_status` 值 `{"key":"yes"}`）、`||` 多值拆分**已由 `scripts/profile.py` 在脚本内完成**。AI 拿到的 `fields` 是干净中文值，无需再解析。本文件仅用于排查字段含义或扩展 `profile.py` 取新字段时参考。

> **Q&A 字段归属**：`detail.py` 概要含 `main_summary`(deal_level/overall_tag/personality_summary/profile_summary) + `basic_notes` 的 6 个高优字段（intended_model/budget_range 等，经 parse_note 映射）+ `customer_overview`(closing_probability/customer_type/business_opp_level/core_issue/current_stage/breakthrough_point) + `emotion_state`(current_state/brand_attitude/sales_attitude) + `inferred_tags`/`usage_scenarios` 的 title 列表 + 动机/偏好/抗性聚合名。`reasoning_detail`/evidence/`radar_data` 明细/全部 basic_notes 属性走 `query_detail.py` 深挖（见 `api-spec.md` §5）。

## 1. main_summary — 主摘要

| 字段 | 类型 | 说明 |
|------|------|------|
| `deal_level` | string | 成交等级（A/B/C 等） |
| `overall_tag` | string | 整体标签（如"务实家用型决策者"） |
| `personality_summary` | string | 人格画像摘要 |
| `profile_summary` | string | 客户画像总结 |

## 2. basic_notes — 基础属性

每个属性为对象，含 `value` / `reasoning_summary`（推断依据）/ `is_locked` / `locked_by` / `ai_feedback`。

| 属性键 | 说明 | 枚举值/注意 |
|--------|------|-------------|
| `age` | 年龄段 | 直接展示 |
| `annual_income` | 年收入 | 直接展示 |
| `budget_range` | 预算区间 | 直接展示 |
| `occupation` | 职业 | 直接展示 |
| `intended_brand` | 意向品牌 | 直接展示 |
| `intended_model` | 意向车型 | 直接展示 |
| `purchase_type` | 购车类型 | `replacement`/`first`/`additional` → 需映射中文 |
| `payment_method` | 支付方式 | `loan`/`full` → 需映射中文 |
| `decision_maker` | 决策人 | `self`/`spouse`/`family` 等 → 需映射 |
| `primary_driver` | 主要驾驶人 | 同上 |
| `core_concerns` | 核心关注点 | `space`/`price`/`safety` 等 → 需映射 |
| `driving_preference` | 驾驶偏好 | 可能含 `\|\|` 分隔多值，需拆分 |
| `purchase_purpose` | 购车用途 | `family`/`business`/`commute` 等 → 需映射 |
| `pain_points` | 痛点 | 文本，直接展示 |
| `hobbies` | 爱好 | 文本，直接展示 |
| `emotion` | 情绪特征 | `steady`/`anxious` 等 → 需映射 |
| `communication_rhythm` | 沟通节奏 | 枚举 → 需映射 |
| `follow_up_frequency` | 跟进频率建议 | 直接展示 |
| `trade_in_requirement` | 是否置换 | `yes`/`no` → 需映射 |
| `driver_license_status` | 驾照状态 | ⚠️ 值为 JSON 字符串（如 `{"key":"yes","input_value":""}`），需解析取 `key` |
| `knowledge_of_erev` | 增程认知度 | `basic`/`expert` 等 → 需映射 |
| `title` | 称谓 | `Mr`/`Ms` 等 |
| `phone_number` | 手机号 | 直接展示 |
| `personality_and_preferences` | 性格偏好 | 枚举 → 需映射 |
| `special_requirements` | 特殊需求 | 文本，直接展示 |

> 枚举值映射表从 `GET /api/v1/remote/config/note-attributes` 获取（详见 `api-spec.md` §3.2）。

## 3. inferred_tags — 推断标签

数组，每项含 `title`（标签名）/ `desc`（描述）/ `ai_feedback`。

## 4. usage_scenarios — 用车场景

数组，每项含 `title` / `desc` / `ai_feedback`。

## 5. customer_overview — 客户总览

| 字段 | 说明 |
|------|------|
| `customer_type` | 客户类型 |
| `closing_probability` | 成交概率 |
| `business_opp_level` | 商机等级 |
| `current_stage` | 当前阶段 |
| `core_issue` | 核心问题 |
| `breakthrough_point` | 突破点 |

各字段配套 `*_reasoning`（推理过程，结构同 reasoning_detail）。

## 6. emotion_state — 情绪状态

| 字段 | 说明 |
|------|------|
| `current_state` | 当前状态 |
| `brand_attitude` | 品牌态度 |
| `sales_attitude` | 销售态度 |
| `radar_data` | 雷达图数据 |

`radar_data.items` 每项含 `score`（0-100）+ `dimension`（维度名）。`radar_data.max_score` 为满分基准。各字段配套 `*_reasoning`。

## 7. purchase_motivations / product_preferences / resistances

### purchase_motivations（购买动机）

数组，按 `rank_order` 排序。每项含 `rank_order` / `motivation_name` / `description` / `reasoning_detail`。

### product_preferences（产品偏好）

数组，每项含 `preference_name` / `description` / `reasoning_detail`。

### resistances（抗拒点）

数组，每项含 `resistance_name` / `severity`（强/中/弱）/ `description` / `reasoning_detail`。

## reasoning_detail 通用结构

各模块的推理过程统一结构：

```json
{
  "steps": ["分析步骤1", "分析步骤2"],
  "summary": "推理总结",
  "evidence": ["证据1", "证据2"],
  "confidence": 0.95
}
```

> 卡片渲染时只取 `summary`。`steps`/`evidence`/`confidence` 仅在销售追问时展示。
