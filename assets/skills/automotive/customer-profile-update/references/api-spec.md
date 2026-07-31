# 客户画像系统 — API 规范

**Base URL：** `https://mhero.dfmc.com.cn/customer_profile/m2m_api`
**鉴权：** 请求头 `X-API-Key`，值通过 sidecar 获取：`GET http://localhost:8004/secret?skill=customer-profile-update&key=api_key`
**OpenAPI 文档：** `http://118.145.238.50:8084/docs`

---

## 1. 分页查询客户画像列表（模糊检索）

`GET /api/v1/remote/data/profiles`

根据手机号片段或客户名称模糊检索。通常作为流程第一步。

**Query 参数：**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `phone_keyword` | string | ❌ | — | 手机号片段（如 `5678`） |
| `customer_name_keyword` | string | ❌ | — | 客户名称关键词 |
| `page` | integer | ❌ | 1 | 页码 |
| `size` | integer | ❌ | 10 | 每页条数，1-100 |

> 至少提供一个 keyword。建议 `size=100` 一次性拉取全部结果。

**响应（200）：**

```json
{
  "total": 1,
  "today_updated_total": 0,
  "page": 1,
  "size": 100,
  "items": [
    {
      "id": 49,
      "phone": "13912345678",
      "name": "客户5678",
      "gender": "未知",
      "age": "45 ~ 55",
      "deal_level": "B",
      "profile_sync_status": 0,
      "overall_tag": "尊享型资深商务客",
      "updated_at": "2026-04-23T14:41:53",
      "is_auto_generated": 1,
      "is_read": 1,
      "is_followed": 0,
      "sales_rep": "<销售代表标识>",
      "has_invitation_analysis": false,
      "invitation_analysis_status": null
    }
  ]
}
```

**`items[]` 关键字段：**

| 字段 | 说明 |
|------|------|
| `id` | 客户记录 ID |
| `phone` | 完整手机号（查询详情用） |
| `name` | 客户名称 |
| `deal_level` | 成交等级（A/B/C） |
| `profile_sync_status` | 画像同步状态，0=可能未生成 |
| `overall_tag` | 整体标签 |
| `sales_rep` | 归属销售（权限校验用） |

> 权限：后端基于 API Key 关联的销售身份或 `sales_rep` 字段过滤。

**无命中（200）：** `total: 0, items: []`

---

## 2. 获取客户完整画像

`GET /api/v1/remote/data/profile/{phone}`

按手机号查询客户完整画像（AI 分析后的多维结构化结果）。

**响应（200）顶层结构：**

```json
{
  "main_summary": { "deal_level": "A", "overall_tag": "...", "personality_summary": "...", "profile_summary": "..." },
  "basic_notes": { "age": {"value":"...","reasoning_summary":"..."}, "budget_range": {"value":"..."}, "..." : {} },
  "inferred_tags": [ { "title": "...", "desc": "..." } ],
  "usage_scenarios": [ { "title": "...", "desc": "..." } ],
  "customer_overview": { "customer_type": "...", "closing_probability": "85%", "..." : {}, "*_reasoning": {} },
  "emotion_state": { "current_state": "...", "radar_data": {...}, "*_reasoning": {} },
  "purchase_motivations": [ { "rank_order": 1, "motivation_name": "...", "reasoning_detail": {} } ],
  "product_preferences": [ { "preference_name": "...", "reasoning_detail": {} } ],
  "resistances": [ { "resistance_name": "...", "severity": "中", "reasoning_detail": {} } ]
}
```

> 各模块字段含义和渲染注意详见 `references/profile-model.md`。

**reasoning_detail 通用结构：**

```json
{ "steps": ["..."], "summary": "...", "evidence": ["..."], "confidence": 0.95 }
```

> 卡片渲染时只取 `summary`，完整 reasoning 仅在销售追问时展示。

---

## 3. 辅助接口

### 3.1 获取客户历史资料记录

`GET /api/v1/remote/data/history/{phone}`

查询客户历史全量有效资料记录。用于画像缺失排查（`GET /profile/{phone}` 返回空时确认是否曾上传过素材）。

### 3.2 获取画像属性配置定义

`GET /api/v1/remote/config/note-attributes`

获取 `basic_notes` 各属性的枚举值和约束。用于枚举值映射中文（如 `replacement`→"换购"）。建议首次渲染画像卡时调用一次，缓存映射表。

---

## 错误处理

| 场景 | 话术 | 恢复路径 |
|------|------|---------|
| 客户不存在（total=0） | "未找到匹配的客户。请确认手机号或姓名，或确认该客户是否归属您。" | 「重新搜索」+「结束」按钮 |
| 权限不足（HTTP 403） | "该客户可能不归属您，无法查询。" | 「重新搜索」按钮 |
| 客户存在但无画像（profile 返回空） | "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。" | 「结束」按钮 |
| 画像同步未完成（profile_sync_status=0） | "该客户画像正在生成中，请稍后再查。" | 「稍后查询」+「查看其他客户」按钮 |
| API Key 无效（HTTP 401） | "系统暂时无法访问客户数据，请稍后重试或联系管理员。" | 不暴露技术细节 |
| 服务器错误（HTTP 500） | "系统繁忙，请稍后重试。" | 「重试」按钮，连续2次失败提示联系管理员 |

**错误码速查：**

| HTTP | 说明 | 是否向销售暴露 |
|------|------|--------------|
| 200 | 成功 | — |
| 422 | 参数校验错误 | ❌ 通用话术 |
| 401 | API Key 无效 | ❌ 通用话术 |
| 403 | 权限不足 | 用"客户不归属您"话术 |
| 500 | 服务器内部错误 | ❌ "系统繁忙"话术 |

---

## 4. `search.py` / `profile.py` 结构化输出（供 AI 建卡 + 校验器消费）

两脚本调上述 API + sidecar 取 Key，stdout 输出结构化 JSON（**不是卡片**）。AI 读它手写卡片，校验器读它核对数据真实性。

### search.py（检索）

```json
{"ok": true, "total": N, "items": [{"id":49,"phone":"13912345678","name":"客户5678","deal_level":"B","profile_sync_status":1,"overall_tag":"..."}], "query": {...}, "hints": {"count_category": "none|single|multi", "returned": N}}
{"ok": false, "error": "auth_fail|forbidden|api_fail|timeout|no_keyword"}
```

| 字段 | 说明 |
|------|------|
| `ok` | `true`=取数成功；`false`=失败（`error` 供 AI 选话术） |
| `total` / `items` | 命中总数 / 精简字段列表（`id` 用于 `select_{id}`，`phone` 用于查画像） |
| `hints.count_category` | `none`/`single`/`multi`，决定走 0命中/直出画像/选择卡 |

### profile.py（画像 + 枚举映射）

```json
{"ok": true, "has_profile": true, "phone": "...", "customer_name": "...",
 "update_url": "https://mhero.dfmc.com.cn/customer_profile/customer/{phone}/profile",
 "fields": {"deal_level":"A","overall_tag":"...","personality_summary":"...","intended_model":"追光","budget_range":"30-40万","current_stage":"需求确认","breakthrough_point":"...","motivations":"...","preferences":"...","resistances":"..."},
 "hints": {"has_profile": true}}
{"ok": true, "has_profile": false, "phone":"...", "update_url":"...", "fields": {}}   # 客户存在但无画像
{"ok": false, "error": "auth_fail|forbidden|api_fail|timeout"}
```

| 字段 | 说明 |
|------|------|
| `has_profile` | `false` 时 AI 回复"暂无画像记录"话术，不建卡 |
| `update_url` | 画像卡「查看完整画像」跳转（`jump_list` + `card_action`）必须用此 url；校验器核对，编造即弃稿 |
| `fields` | 已完成枚举映射 + JSON 字符串解析 + `||` 拆分的干净值，AI 直接布局 |

> API Base / 更新页 Base 可分别用环境变量 `PROFILE_API_BASE` / `PROFILE_UPDATE_BASE` 覆盖。

---

## 5. `detail.py` / `query_detail.py` 结构化输出（详情问答用）

`detail.py` 调 `GET /profile/{phone}`（复用 profile.py 取数原语）+ 把完整结果存 `$HERMES_HOME/.skill_tmp/cp_detail_{phone}.json`(0600)，stdout **只返回 ~5-9KB 概要**（避免 90KB 全量进入会话历史）。`query_detail.py` 从磁盘文件按主题取深挖切片。

> 画像可变：`detail.py` 每次运行都从 API 现取（只写不读缓存）；`query_detail.py` 读前查文件 mtime，超 10min 返 `cache_missing` 触发重取。

### detail.py（概要 + 存盘）

```bash
python3 detail.py --phone <完整手机号> [--customer-name <客户名>] > "$HERMES_HOME/.skill_tmp/cp_brief.json"
```

```json
{"ok": true, "phone": "139****5678", "customer_name": "...", "update_url": "...",
 "fetched_at": "2026-07-06T10:00:00", "updated_at": "...",
 "stored_at": "$HERMES_HOME/.skill_tmp/cp_detail_<phone>.json",
 "brief": {
   "deal_level": "A", "overall_tag": "...", "personality_summary": "...",
   "intended_model": "追光", "budget_range": "30-40万",
   "current_stage": "...", "breakthrough_point": "...",
   "motivations": "...", "preferences": "...", "resistances": "...",
   "profile_summary": "...",
   "closing_probability": "85%", "customer_type": "...", "business_opp_level": "...", "core_issue": "...",
   "emotion_current_state": "...", "brand_attitude": "...", "sales_attitude": "...",
   "inferred_tags": ["..."], "usage_scenarios": ["..."]
 },
 "topics": ["basic_notes_detail","customer_overview_detail","emotion_detail",
            "motivations_detail","preferences_detail","resistances_detail",
            "inferred_tags","usage_scenarios","personality"],
 "hints": {"has_main_summary": true, "has_basic_notes": true, "has_customer_overview": true,
           "has_emotion_state": true, "has_motivations": true, "has_preferences": true,
           "has_resistances": true, "has_inferred_tags": true, "has_usage_scenarios": true}}
{"ok": true, "has_profile": false, "phone": "...", "customer_name": "..."}
{"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}
```

| 字段 | 说明 |
|------|------|
| `brief` | 高信号摘要，答 ~80% 常见问题；复用 profile.py `extract_fields` + 扩展 customer_overview/emotion_state/标签/场景 |
| `topics` | `query_detail.py` 支持的深挖主题清单 |
| `hints.has_*` | 模块是否生成（null → 该模块问答答「未生成」）|
| `fetched_at` | 取数时间戳（ISO）；AI 据此判断概要是否需刷新（10min TTL）|
| `updated_at` | 画像 API 的 updated_at（若提供），销售可见画像上次更新时间 |
| `stored_at` | 完整结果磁盘路径（query_detail.py 按 phone 读，不直接用此路径）|

### query_detail.py（深挖切片）

```bash
python3 query_detail.py --phone <完整手机号> --topic <topic>
```

```json
{"ok": true, "phone": "...", "topic": "...", "data": <切片>}
{"ok": true, "phone": "...", "topic": "...", "data": null,
 "message": "该客户的<模块>模块尚未生成"}                      # 依赖模块 null
{"ok": false, "error": "cache_missing", "phone": "...",
 "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
{"ok": false, "error": "unknown_topic", "available": [...]}
```

**主题清单（`--topic`）：**

| 主题 | 内容 | 依赖模块 |
|------|------|---------|
| `basic_notes_detail` | 全部 basic_notes 属性（parse_note 映射 value + reasoning_summary）| basic_notes |
| `customer_overview_detail` | 完整 customer_overview + `*_reasoning` | customer_overview |
| `emotion_detail` | emotion_state + `radar_data`(items/max_score) + `*_reasoning` | emotion_state |
| `motivations_detail` | `purchase_motivations[]` + `reasoning_detail` | purchase_motivations |
| `preferences_detail` | `product_preferences[]` + `reasoning_detail` | product_preferences |
| `resistances_detail` | `resistances[]` + `severity` + `reasoning_detail` | resistances |
| `inferred_tags` | `[{title, desc}]` | inferred_tags |
| `usage_scenarios` | `[{title, desc}]` | usage_scenarios |
| `personality` | main_summary 完整（含 `profile_summary`）| main_summary |

> `--topic help` 列出全部可用主题。文件缺失/过期（mtime 超 10min / pod 重启）→ `cache_missing`，重跑 `detail.py` 即可。`basic_notes_detail` 复用 profile.py `parse_note` 做枚举映射（`replacement`→"换购" 等）。

