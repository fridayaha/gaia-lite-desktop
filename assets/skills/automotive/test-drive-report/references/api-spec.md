# 试驾报告系统 — API 规范

**Base URL：** `https://mhero.dfmc.com.cn/drive-insight/backend`

**鉴权方式：** 所有请求需携带 `X-API-Key` 头。API Key 经 sidecar 解密（`scripts/auth.py` 的 `get_api_key()` 从 sidecar 取明文），由 `run.py` / `detail.py` 自动注入，无需调用方手动处理。缺失/无效返回 HTTP 401。

**OpenAPI 文档：** `https://mhero.dfmc.com.cn/drive-insight/backend/apidocs/`

---

## 1. 查询试驾报告列表（模糊检索）

**端点：** `GET /api/test_drive_reports`

**说明：** 查询 MySQL `analysis_task` 表中 `status=completed` 的试驾报告。作为查询流程的唯一入口，既用于模糊检索定位目标报告，也直接返回报告卡片所需的全部字段。

**匹配规则：**

- `sales_phone` 必填，销售顾问手机号**精确匹配**
- `customer_name` / `customer_phone` 可选，**模糊匹配**（`LIKE %keyword%`），支持尾号（如 `8476`）
- `drive_date` 可选，按数据库 `start_time` **前缀匹配到天**；**未传时默认今天**
- `report_url` 由服务端动态拼接为 `NOTIFY_REPORT_BASE_URL/{test_drive_id}`

> ⚠️ API 只支持到日期，不支持按时段（上午/下午）过滤。销售提到时段时，按当天日期查返回全部报告，卡片用 🕐 上午/下午 标注区分（run.py 不做客户端时段过滤——会漏数据）。

**Query 参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `sales_phone` | string | ✅ | — | 销售顾问手机号（精确匹配） |
| `customer_name` | string | ❌ | — | 客户姓名模糊匹配 |
| `customer_phone` | string | ❌ | — | 客户手机号模糊匹配（支持尾号，如 `8476`） |
| `drive_date` | string | ❌ | 今天 | 试驾日期 `YYYY-MM-DD`，按 `start_time` 前缀匹配 |
| `limit` | integer | ❌ | 20 | 每页数量，1-100 |
| `offset` | integer | ❌ | 0 | 偏移量，从 0 开始 |

**请求示例：**

```bash
curl -s -X GET "https://mhero.dfmc.com.cn/drive-insight/backend/api/test_drive_reports?sales_phone=12345678901&customer_name=王&customer_phone=5538&drive_date=2026-05-27&limit=20&offset=0"
```

**成功响应（200）：**

```json
{
  "code": 0,
  "data": {
    "drive_date": "2026-05-27",
    "total": 1,
    "items": [
      {
        "customer_name": "王先生",
        "customer_phone": "176****5538",
        "start_time": "2026-05-27 13:59:57",
        "end_time": "2026-05-27 16:05:52",
        "test_drive_id": "1610926234373631604",
        "vehicle": "M817-城市科技版A",
        "vehicle_model": "M817",
        "vehicle_variant": "城市科技版A",
        "report_url": "http://127.0.0.1/drive-insight/report/test-drive/1610926234373631604"
      }
    ]
  },
  "message": "ok"
}
```

**响应字段说明：**

| 顶层字段 | 类型 | 说明 |
|---------|------|------|
| `code` | integer | 状态码，`0`=成功，非 0=失败 |
| `message` | string | 状态描述 |
| `data.drive_date` | string | 实际使用的试驾日期（筛选值，未传时为今天） |
| `data.total` | integer | 符合筛选条件的记录总数 |
| `data.items` | array | 报告列表，见下表 |

| `items[]` 字段 | 类型 | 说明 |
|---------------|------|------|
| `test_drive_id` | string | 试驾报告唯一标识（选择卡片使用此值作为 `id`） |
| `customer_name` | string | 客户姓名 |
| `customer_phone` | string | 客户手机号（完整，展示时需脱敏） |
| `start_time` | string | 试驾开始时间，`YYYY-MM-DD HH:MM:SS` 或 `YYYY-MM-DDTHH:MM:SS` |
| `end_time` | string | 试驾结束时间，格式同上 |
| `vehicle` | string | 车型展示字段（"车型-配置"组合，如 `M817-城市科技版A`） |
| `vehicle_model` | string | 车型代号 |
| `vehicle_variant` | string | 配置版本 |
| `report_url` | string | 报告详情页完整 URL，点击跳转 |

**无记录响应（200）：**

```json
{
  "code": 0,
  "data": {
    "drive_date": "2026-05-27",
    "total": 0,
    "items": []
  },
  "message": "ok"
}
```

**错误响应：**

```json
// 400 参数错误
{
  "code": 1,
  "message": "参数错误：sales_phone 不能为空"
}

// 500 服务器内部错误
{
  "code": 1,
  "message": "服务器内部错误"
}
```

---

## 2. 时段（仅显示，不过滤）

API 只支持到日期，**不支持按时段过滤**。run.py **不做客户端时段过滤**（客户端过滤只过滤 API 返回的那批，会漏掉没进 batch 的同时段报告）。

销售提到"上午/下午"时：按当天 `drive_date` 查 → 返回全部报告 → 卡片用 🕐 上午/下午 标注（基于 `start_time` 小时，`<12` 上午 / `≥12` 下午）帮销售区分。`hints.cross_time_slot` 标记 items 是否跨上下午（触发"时段分组卡"显示）。

> `start_time` 可能是 `YYYY-MM-DD HH:MM:SS`（空格分隔）或 `YYYY-MM-DDTHH:MM:SS`（ISO T 分隔），`run.py` 的 `parse_hour()` 已兼容两种格式（仅用于 hints/显示，不用于过滤）。

---

## 3. `run.py` 结构化输出（供 AI 建卡 + 校验器消费）

`run.py` 调上述 API + 计算 hints（自动 tee：stdout 给 agent + 写 tdr.json 给校验器），输出结构化 JSON（**不再是卡片**）：

```json
{
  "ok": true,
  "code": 0,
  "total": 3,
  "items": [ /* 同上 items[] */ ],
  "hints": {
    "cross_time_slot": true,
    "same_customer_multi_car": false,
    "count_category": "multi"
  },
  "query": {
    "sales_phone": "...", "customer_name": "王", "customer_phone": null,
    "drive_date": null
  }
}
```

| 字段 | 说明 |
|------|------|
| `ok` | `true`=取数成功；`false`=API 异常/`code!=0`（附带 `error:"api_fail"`） |
| `code` | 透传 API 状态码 |
| `total` | API 返回的符合条件总数（全量，未过滤） |
| `items` | API 返回的报告列表（输出截到 6 条；字段同 §1 items[]） |
| `hints.cross_time_slot` | items 是否跨上下午（列表卡每行带时段的依据） |
| `hints.same_customer_multi_car` | 同一 `customer_phone` 是否有多个不同 `vehicle_model` |
| `hints.count_category` | 命中量级：`none`/`single`/`multi`(2-6)/`many`(>6) |
| `query` | 实际使用的查询参数（回显） |

AI 读 `items` + `hints` 选卡片形态，手写卡片 JSON 后交 `validate_card.py` 校验（stdin 喂本结构）。

---

## 4. 查询试驾报告详情（聚合分析）

**端点：** `GET /api/drive_analysis`

**说明：** 一次返回指定试驾下三大模块的全部已生成结果，供前端一次渲染，也供本 skill 的「详情问答」能力取数。

**匹配规则：**

- `test_drive_id` 与 `customer_phone` 至少提供一个；同时提供时优先 `test_drive_id`
- `customer_phone` 查询时取最新一条 ASR 记录
- `sales_phone` 可选，用于过滤（权限防御）

**Query 参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `test_drive_id` | string | ❌* | 试驾业务 ID（来自 run.py items）|
| `customer_phone` | string | ❌* | 客户手机号（取最新一条）|
| `sales_phone` | string | ❌ | 销售顾问手机号（可选，过滤）|

> *`test_drive_id` / `customer_phone` 至少一个。本 skill 的 Q&A 流程总用 `test_drive_id`（从列表卡片状态取）。

**响应（200）：** `result` 字段是 **JSON 字符串**（需 `json.loads`），解析后顶层结构：

```json
{
  "test_drive_id": "1610926234373631604",
  "audio_filename": "12281777879575992.mp3",
  "customer_phone": "17621765538",
  "sales_check": { "<dim>": {"result": {...}, "updated_at": "..."} },
  "deal_intent": { "result": {...}, "updated_at": "..." },
  "next_action": { "result": {...}, "updated_at": "..." }
}
```

**三模块：**

| 模块 | 内容 | null 语义 |
|------|------|-----------|
| `sales_check` | 销售检核，固定 4 维度键 `communication`/`knowledge`/`deal_guide`/`process`，未生成维度值为 `null` | 模块总存在；维度可 null |
| `deal_intent` | 成交意愿：`closerDashboard`(closeLevel/closeProbability/stage/aiInsight/painPoints)、`focusAnalysis`(items+evidence+rationaleChain)、`emotionHeatmap`、`signalsAndRisks`、`resistanceAnalysis` | 整个模块可为 `null` |
| `next_action` | 下一步建议：`timeline`、`nextBestAction`(actions/followUpScript/aiReminder)、`customerProfile`(tags/riskAlert/winLossDrivers)、`salesAmmo`、`competitorCard` | 整个模块可为 `null` |

> 各 `result` 内还有 `*_reasoning` / `reasoning_detail`(steps/evidence/confidence) 等深挖字段，问答时按需取。

**错误响应：**

| HTTP | code | 说明 |
|------|------|------|
| 200 | 0 | 成功（`result` 空=分析未生成）|
| 400 | 1 | 参数错误（test_drive_id 与 customer_phone 都没给）|
| 404 | 1 | 记录不存在 |

**OpenAPI：** `https://mhero.dfmc.com.cn/drive-insight/backend/apispec.json`，路径 `#/paths/~1api~1drive_analysis`

---

## 5. `detail.py` / `query_detail.py` 结构化输出（详情问答用）

`detail.py` 调上述 API + 把完整结果存 `$HERMES_HOME/.skill_tmp/tdr_detail_{id}.json`（0600），stdout **只返回 ~5-9KB 概要**（避免 90KB 全量进入会话历史）。`query_detail.py` 从磁盘文件按主题取深挖切片。

### detail.py（概要 + 存盘）

```bash
python3 detail.py --test-drive-id <id> > "$HERMES_HOME/.skill_tmp/tdr_brief.json"
# sales_phone 由 detail.py 自动从 USER.md「业务手机号」读取，始终作为权限过滤传 API
```

```json
{"ok": true, "test_drive_id": "...", "customer_phone": "176****5538", "vehicle": "M817",
 "stored_at": "$HERMES_HOME/.skill_tmp/tdr_detail_<id>.json",
 "updated_at": {"sales_check": "...", "deal_intent": "...", "next_action": "..."},
 "brief": {
   "deal_intent": {"close_level":"A","close_probability":65,"stage":"...","ai_insight":"...",
                   "pain_points":[...],"focus_summary":"...","focus_items":[{"dimension","weight","details"}],
                   "signal_strength":65,"risks":[...],"explicit_signals":[...],"implicit_signals":[...],
                   "resistance_summary":"..."},
   "sales_check": {"<dim>":{"score_rate":"81.0%","overall_evaluation":"...","improvement_suggestions":[...]}},
   "next_action": {"actions":[...],"follow_up_script":"...","ai_reminder":[...],
                   "customer_tags":[...],"risk_alert":"...","recommended_kit":"...","competitor":"..."}
 },
 "topics": ["sales_check_detail","deal_intent_detail",...],
 "hints": {"has_sales_check": true, "has_deal_intent": true, "has_next_action": true}}
{"ok": false, "error": "not_found"|"not_generated"|"api_fail"|"timeout"|"bad_request"}
```

| 字段 | 说明 |
|------|------|
| `brief` | 高信号摘要，答 ~80% 常见问题；`focus_items` 已舍 evidence/rationaleChain |
| `topics` | `query_detail.py` 支持的深挖主题清单 |
| `hints.has_*` | 模块是否生成（null → 该模块问答答「未生成」）|
| `stored_at` | 完整结果磁盘路径（query_detail.py 按 test_drive_id 读，不直接用此路径）|

### query_detail.py（深挖切片）

```bash
python3 query_detail.py --test-drive-id <id> --topic <topic>
```

```json
{"ok": true, "test_drive_id": "...", "topic": "...", "data": <切片>}
{"ok": true, "topic": "...", "data": null, "message": "该报告的<模块>模块尚未生成"}
{"ok": false, "error": "cache_missing", "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
{"ok": false, "error": "unknown_topic", "available": [...]}
```

**主题清单（`--topic`）：**

| 主题 | 内容 | 依赖模块 |
|------|------|---------|
| `sales_check_detail` | 完整 sales_check（含 groups/analysis_items/highlights）| sales_check |
| `deal_intent_detail` | 完整 deal_intent.result（含 evidence/rationaleChain/emotionHeatmap）| deal_intent |
| `next_action_detail` | 完整 next_action.result（含 timeline/winLossDrivers/materials）| next_action |
| `improvement_suggestions` | 四维度改进建议聚合 | sales_check |
| `focus_analysis` | focusAnalysis（含 evidence + rationaleChain）| deal_intent |
| `resistance` / `signals_risks` | 抗拒分析 / 信号与风险 | deal_intent |
| `timeline` / `emotion_heatmap` | 试驾时间线 / 情绪热度 | next_action / deal_intent |
| `competitor` / `customer_profile` / `sales_ammo` | 竞品卡 / 客户画像 / 销售弹药 | next_action |
| `pain_points` / `actions` | 痛点 / 下一步动作 | deal_intent / next_action |

> `--topic help` 列出全部可用主题。文件缺失（pod 重启/超时）→ `cache_missing`，重跑 `detail.py` 即可。


