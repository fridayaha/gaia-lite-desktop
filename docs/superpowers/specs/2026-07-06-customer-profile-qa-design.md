# 客户画像 Skill — 详情问答能力设计

- 日期：2026-07-06
- Skill：`assets/skills/automotive/customer-profile-update`
- 版本：2.1.0 → 2.1.1

## 背景与问题

现有 skill 只做「模糊检索 → 选择客户 → 画像卡（button_interaction）→ 更新画像跳外部系统」，画像卡只展示 6 个高优先级字段。销售若想追问画像详情（成交概率 / 客户类型 / 情绪状态 / 推断标签 / 用车场景 / 动机与抗性的推理依据 / 雷达图明细等），只能去外部系统翻完整画像。

画像完整内容来自 `GET /api/v1/remote/data/profile/{phone}`（7 大模块：`main_summary` / `basic_notes` / `inferred_tags` / `usage_scenarios` / `customer_overview` / `emotion_state` / `purchase_motivations`+`product_preferences`+`resistances`）。`profile.py` 现已调该接口但只提取 10 个扁平字段给卡片用，丢弃了其余 6 模块与全部 `reasoning_detail`。

**核心问题**：完整画像 ~90KB+，若每轮全量喂进 Hermes 会话历史，N 轮后历史线性膨胀撑爆上下文。

## 与试驾报告 skill 的关键差异

试驾报告**不可变**（completed 即定稿），故 test-drive skill 的 `detail.py` 存盘后可无限复用概要。

**客户画像是可变的**——销售点「更新画像」跳外部系统传素材后，AI 会重新生成画像。若照搬 test-drive「存盘 + 后续问答直取本地」，存在陈旧风险。本设计引入 **TTL 限定复用**解决（见「缓存与新鲜度策略」）。

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| detail.py 与 profile.py 分工 | **additive**：新增 detail.py 复用 profile.py 原语，profile.py 不动 | 镜像 test-drive，卡片流程零回归 |
| 概要信息密度 | **富概要**（~5-9KB，答 80%）| 画像 7 模块，富概要覆盖常见综合问题，深挖只用于 reasoning_detail / 雷达图明细 |
| 缓存与枚举映射 | 缓存存 `{"profile":<raw>, "enum_map":<map>, "fetched_at":<iso>}`；query_detail import `parse_note`，basic_notes 主题现映射 | 缓存忠于 API 响应；复用 profile.py 映射逻辑；query_detail 免鉴权 |
| 新鲜度 | **TTL 限定复用**（10min）| 画像可变，需限定复用窗口；跨会话由会话超时自然解决，会话内由 TTL + 显式刷新兜底 |

## 组件

| 文件 | 动作 | 职责 |
|------|------|------|
| `scripts/detail.py` | 新增 | `from profile import get_api_key, fetch_profile, fetch_enum_map, extract_fields, parse_note`。取完整画像 + enum_map → 存 `/tmp/cp_detail_{phone}.json`（0600）→ stdout 返富概要（含 `fetched_at`）|
| `scripts/query_detail.py` | 新增 | 纯文件读（免鉴权），按 `--topic` 取切片。先查文件 mtime > 10min → `cache_missing`；basic_notes 主题用 `parse_note` 现映射，其余返原始模块 |
| `SKILL.md` | 改 | 加 Q&A 流程、触发识别、状态跟踪、新鲜度规则、防幻觉 |
| `manifest.json` | 改 | 版本 2.1.0→2.1.1，description 补详情问答 |
| `references/api-spec.md` | 改 | 补 detail.py / query_detail.py 输出 schema（profile 接口已在 §2）|
| `references/profile-model.md` | 改 | 加 Q&A 用法说明（哪些字段进概要 / 哪些进深挖）|
| `scripts/tests/test_detail.py` | 新增 | detail.py 测试 |
| `scripts/tests/test_query_detail.py` | 新增 | query_detail.py 测试 |
| `profile.py` / `search.py` / `validate_card.py` / `build_card.py` | 不动 | 检索 / 卡片流程不变 |

## 数据流

```
[检索轮] search.py → 选择卡 → profile.py → 画像卡
          状态: last_action=showed_profile, current_customer={customer_id, phone, name, level}
[首轮问答] AI 判定为追问 → 用 current_customer.phone（画像卡展示后已设，无多命中歧义）
          → detail.py 现取画像+enum_map → 存盘 + 返富概要(带 fetched_at) → AI 文本作答
          → 状态: last_action=in_profile_qa
[后续问答] 概要够答且 fetched_at 在 10min 内 → 复用历史概要作答（不重取）
          概要不够 → query_detail.py --topic X 取切片
                     → 若 cache_missing（mtime>10min）→ 重跑 detail.py 刷新概要+缓存 → 重跑 query_detail
          销售说"刷新画像/取最新" → 无视 TTL 重跑 detail.py
[新检索] search.py → 覆盖状态
[取消/换一个/10min超时] → 清状态
```

## 缓存与新鲜度策略（核心差异）

**detail.py 是新鲜度闸门**——每次运行都从 API 现取（只写缓存、不读缓存服务概要）。磁盘缓存是 query_detail 用的会话级工作副本，不是长期缓存。

### 新鲜度窗口

| 场景 | 是否新鲜 | 机制 |
|------|---------|------|
| 跨会话（隔时再来）| ✅ | 会话超时 10min 清状态 → 新会话首轮必跑 detail.py 现取覆盖缓存 |
| 会话内短时追问（<10min）| ✅ | 画像更新是重操作（跳外部系统 + 传素材 + AI 重生成，分钟级），会话进行中风险极低 |
| 会话内长时追问（>10min）| ⚠️→✅ | query_detail 查 mtime > 10min → `cache_missing` → 触发 detail.py 重取；brief 带 `fetched_at` 可见，销售可显式刷新 |

### 三层兜底

1. **TTL 限定复用（10min）**：
   - query_detail 读缓存前查文件 mtime，超 10min 返 `cache_missing`（不返陈旧数据）→ AI 重跑 detail.py 刷新概要 + 缓存 → 重跑 query_detail。深挖路径自愈。
   - 概要复用：brief 带 `fetched_at`，AI 见 `fetched_at` 跨小时/隔天则重跑 detail.py（软约束，跨会话已由超时硬保证）。
2. **`updated_at` + `fetched_at` 可见**：brief 带画像 API 的 `updated_at`（若提供）+ 我们的 `fetched_at`，销售可见"画像上次更新于 X，取数于 Y"。
3. **显式刷新意图**：销售说"刷新画像 / 取最新画像 / 画像是不是最新的" → 无视 TTL 直接重跑 detail.py。

### 与 test-drive 对比

| | test-drive（不可变）| customer-profile（可变）|
|---|---|---|
| 概要复用 | 无限复用（报告定稿）| 10min TTL 内复用，超时重取 |
| 缓存 TTL | 无（永久有效）| 10min mtime 检查 |
| 刷新意图 | 不需要 | 显式"刷新画像"触发重取 |

## 概要内容（~5-9KB，答 80%）

复用 `extract_fields` 的 10 字段 + 扩展：

- **复用 extract_fields**：`deal_level` / `overall_tag` / `personality_summary` / `intended_model`(映射) / `budget_range`(映射) / `current_stage` / `breakthrough_point` / `motivations`(聚合名) / `preferences`(聚合名) / `resistances`(聚合名)
- **新增高信号字段**：
  - `profile_summary`（main_summary 完整总结）
  - `closing_probability` / `customer_type` / `business_opp_level` / `core_issue`（customer_overview）
  - `emotion_current_state` / `brand_attitude` / `sales_attitude`（emotion_state）
  - `inferred_tags: [title...]`（推断标签标题）
  - `usage_scenarios: [title...]`（用车场景标题）
- **元信息**：`phone`(脱敏) / `customer_name` / `update_url` / `fetched_at` / `updated_at`(若 API 提供) / `topics[]` / `hints.has_{main_summary,basic_notes,customer_overview,emotion_state,motivations,preferences,resistances,inferred_tags,usage_scenarios}`

## 深挖主题（query_detail.py，9 个）

| topic | 内容 | 依赖模块 |
|-------|------|---------|
| `basic_notes_detail` | 全部 ~24 属性（parse_note 现映射值 + `reasoning_summary`）| basic_notes |
| `customer_overview_detail` | 完整 customer_overview + `*_reasoning` | customer_overview |
| `emotion_detail` | emotion_state + `radar_data`（items/max_score）+ `*_reasoning` | emotion_state |
| `motivations_detail` | `purchase_motivations[]` + `reasoning_detail` | purchase_motivations |
| `preferences_detail` | `product_preferences[]` + `reasoning_detail` | product_preferences |
| `resistances_detail` | `resistances[]` + `severity` + `reasoning_detail` | resistances |
| `inferred_tags` | `[{title, desc}]` | inferred_tags |
| `usage_scenarios` | `[{title, desc}]` | usage_scenarios |
| `personality` | main_summary 完整（含 `profile_summary`）| main_summary |

模块缺失（依赖模块为 null/空）→ `data=null` + "该报告的<模块>模块尚未生成"；缓存过期 → `cache_missing`；未知 topic → `unknown_topic` + 列可用主题。`--topic help` 列全部。

## 状态跟踪

复用现有状态机，新增 Q&A 态：

| 字段 | 用途 |
|------|------|
| `current_customer` | 复用现有 `{customer_id, phone, name, level}`；`phone` 作缓存 key |
| `last_action` | 扩展 `in_profile_qa`（原有 `searched_list`/`showed_profile`/`null` 不变）|
| `last_active_at` | 10min 超时清空（同现有）|

> 画像卡只展示一个客户，故 Q&A 无 test-drive 的多命中歧义——`current_customer.phone` 在 `showed_profile` 后已设值，直接用。

## 触发识别（AI 路由判断）

`last_action ∈ {showed_profile, in_profile_qa}` 后的下一条消息，AI 据意图判：

- **追问**：问画像内容（"成交概率/客户类型/情绪/标签/场景/动机/抗性/雷达图/突破点/推理依据"）或指代词（"他/这个客户"）→ 进 Q&A
- **新检索**：带新客户标识 + 查询意图（"查 5678 / 王总的画像"）→ 跑 search.py 覆盖状态
- **取消/换一个**：清状态回搜索
- **刷新画像**：在 `in_profile_qa` 态说"刷新/取最新" → 重跑 detail.py（不换客户）

## 防幻觉（Q&A 无校验器）

- 只从 `detail.py` 概要 / `query_detail.py` 切片输出作答，严禁用先验知识补全客户信息
- 概要不够时调 `query_detail.py` 取相关主题，不得凭概要摘要猜深挖细节
- 字段 null / 模块未生成（`hints.has_*=false` 或 `data=null`）→ 说"该客户的 X 模块尚未生成"，不编造
- 数字 / 等级 / 概率逐字引用脚本输出（如"成交概率 85%"、"deal_level A"）
- 长文本（`profile_summary` / `reasoning_detail.summary`）原样引用，不改写

## 错误处理

| 场景 | 话术 | 输出 |
|------|------|------|
| 客户无画像（has_profile=false）| "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。" | 纯文本（不进 Q&A）|
| 404（API 不返 404，折叠为 api_fail）| 走 api_fail 话术 | 纯文本；"未找到画像" UX 由 has_profile=false 覆盖 |
| 401 auth_fail | "系统暂时无法访问客户数据，请稍后重试或联系管理员。" | 纯文本 |
| 403 forbidden | "该客户可能不归属您，无法查询。" | 纯文本 |
| 5xx/timeout | "系统繁忙，请稍后重试。" | 纯文本 |
| 模块 null | "该客户的 X 模块尚未生成。" | 纯文本 |
| 缓存过期（query_detail cache_missing）| 触发重跑 detail.py 刷新后再答 | — |
| 未知 topic | query_detail 列出可用主题 | — |

## 测试

- `test_detail.py`：概要提取（复用 extract_fields + 扩展字段）、存盘（0600）、模块 null、错误分类（401/403/timeout/api_fail，404 折叠为 api_fail）、脱敏、`fetched_at` 注入、概要有界（< 10KB 断言）、完整 JSON 不进 stdout
- `test_query_detail.py`：各 topic 切片、basic_notes 主题枚举映射（验证 parse_note 复用）、缓存缺失、**mtime TTL 过期返 cache_missing**、未知 topic、null 模块
- stdlib unittest，mock `urllib.request.urlopen`，独立于 `make test`（与现有 test_profile.py 同约定）
- 用真实 API 响应结构造 fixture（7 模块齐全 + 部分模块 null 场景）

## 非目标（YAGNI）

- 不做向量 RAG——结构化 JSON，按主题切片更准
- 不改检索/卡片流程——Q&A 是画像卡后的延续，button_interaction 卡片 + 「更新画像」跳转不变
- 不给卡片加问答按钮——保持现有 button_interaction 设计
- 不做跨会话持久缓存——/tmp 会话级，跨会话由超时 + 首轮现取刷新
- 不做增量更新检测（如先查 search.py 的 updated_at 再决定是否重取 full）——画像 API 无轻量 updated_at 端点，TTL + 显式刷新已足够
- 不在 Q&A 轮出卡片——问答输出纯文本/Markdown，与卡片轮形态区分（同 test-drive）
