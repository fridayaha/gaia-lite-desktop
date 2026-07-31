# 试驾报告 Skill — 详情问答能力设计

- 日期：2026-07-06
- Skill：`assets/skills/automotive/test-drive-report`
- 版本：1.1.1 → 1.2.0

## 背景与问题

现有 skill 只做列表检索 + 卡片跳转 `report_url`，无详情问答。新增「基于报告详情的用户问答」，详情来自 `GET /api/drive_analysis`（~90KB 聚合 JSON，含 `sales_check` 销售检核 / `deal_intent` 成交意愿 / `next_action` 下一步建议 三模块）。

**核心问题**：Hermes 会话历史累积每次 `terminal` 工具调用的结果。若每轮全量喂 90KB，N 轮后历史线性膨胀（5 轮 ~450KB / ~150K tokens），撑爆上下文且重复读静态数据。

## 设计：重数据搬出会话历史

90KB 永不进会话历史；磁盘存一份，会话里只放小切片。

### 组件

| 文件 | 动作 | 职责 |
|------|------|------|
| `scripts/detail.py` | 新增 | 调 `GET /api/drive_analysis`，完整结果存 `/tmp/tdr_detail_{id}.json`（0600），stdout 只返回 ~5KB 概要 |
| `scripts/query_detail.py` | 新增 | 读磁盘文件，按 `--topic` 返回深挖切片 |
| `SKILL.md` | 改 | 加 Q&A 流程、触发识别、状态跟踪、防幻觉规则 |
| `manifest.json` | 改 | 版本 1.1.1→1.2.0，description 补详情问答 |
| `references/api-spec.md` | 改 | 补 drive_analysis 接口文档 |
| `run.py` / `validate_card.py` / `build_card.py` | 不动 | 列表/卡片流程不变 |

### 数据流

```
[列表轮] run.py → 卡片(跳report_url) → 状态: last_action=showed_report, 缓存 last_list
[首轮问答] AI 判定为追问 → 定位 test_drive_id:
            total=1 → 直接用; total>1 → 销售带标识匹配 last_list, 无标识→问"哪位"
            → detail.py 取数存盘 → 返回 5KB 概要 → AI 基于概要作答(文本)
            → 状态: last_action=in_report_qa, current_report={test_drive_id,...}
[后续问答] 概要够答 → 复用历史里的概要作答(不重取)
          概要不够 → query_detail.py --topic X 取切片 → 作答
[新检索] run.py → 覆盖状态    [取消/10min超时] → 清状态
```

### 概要内容（~5KB，答 80% 问题）

- `deal_intent` 要点：closeLevel / closeProbability / stage / aiInsight / painPoints / focusSummary + top4 关注项（drops evidence/rationaleChain）/ resistanceSummary / risks / signals
- `sales_check` 四维：每维 score_rate + overall_evaluation + improvement_suggestions（跳过 null 维度）
- `next_action` 要点：actions（优先级+动机+预期影响）/ followUpScript / aiReminder / customerTags / riskAlert / recommendedKit / competitor
- 元信息：test_drive_id、脱敏 customer_phone、vehicle、三模块 updated_at、`topics[]`（可深挖主题清单）

### 深挖主题（query_detail.py）

`sales_check_detail` / `deal_intent_detail` / `next_action_detail` / `improvement_suggestions` / `focus_analysis` / `resistance` / `signals_risks` / `timeline` / `emotion_heatmap` / `competitor` / `customer_profile` / `sales_ammo` / `pain_points` / `actions`

深挖内容含概要里舍去的：analysis_items / groups / highlights / evidence / rationaleChain / emotionHeatmap / timeline / winLossDrivers / salesAmmo.materials 等。

### 状态跟踪（首次给该 skill 引入状态）

| 字段 | 用途 |
|------|------|
| `current_report` | `{test_drive_id, customer_phone, customer_name, vehicle, fetched_at}` |
| `last_action` | 扩展 `showed_report` / `in_report_qa`（原有列表态不变）|
| `last_list` | 缓存最近 run.py items（多命中时反查 test_drive_id）|
| `last_active_at` | 10min 超时清空 |

### 触发识别（AI 路由判断）

`showed_report`/`in_report_qa` 后的下一条消息，AI 据意图判：
- **追问**：问报告内容（"成交概率/表现/建议/关注/抗拒/下一步/风险/竞品/时间线"）或指代词（"他/这个客户/那次试驾"）→ 进 Q&A
- **新检索**：带新客户标识 + 查询意图（"查8476/昨天试驾的王总"）→ 跑 run.py 覆盖状态
- **取消**：「取消/换一个」→ 清状态

### 防幻觉（Q&A 无校验器）

- 只从 `detail.py` 概要 / `query_detail.py` 切片输出作答
- 概要不够时调 `query_detail.py` 取相关主题，不得凭空答
- 字段 null / 模块未生成 → 说「该报告未生成 X 分析」，不编造
- 数字 / 等级 / 得分率逐字引用脚本输出
- 严禁用先验知识补全客户/报告信息

### 错误处理

| 场景 | 话术 | 输出 |
|------|------|------|
| 分析未生成（404 / result 空）| "该试驾的分析报告尚未生成，可能分析还在进行中（生成需要几分钟），请稍后再查。" | 纯文本 |
| 重新定位失败（无 test_drive_id）| "未能定位试驾报告，请重新检索或指明客户。" | 纯文本 |
| API 5xx/超时 | "分析报告查询失败，请稍后重试。" | 纯文本 |
| 模块 null | "该报告的 X 模块尚未生成。" | 纯文本 |
| 缓存缺失（query_detail）| 触发重跑 detail.py | — |
| 未知 topic | query_detail 列出可用主题 | — |

### 测试

- `test_detail.py`：概要提取、存盘、模块 null、错误分类（404/timeout/api_fail/not_generated）、脱敏、概要有界（< 8KB 断言）、完整 JSON 不进 stdout
- `test_query_detail.py`：各 topic 切片、缓存缺失、未知 topic、null 模块
- stdlib unittest，mock `urllib.request.urlopen`，独立于 `make test`（与现有 test_run.py 同约定）
- 用真实 API 响应结构造 fixture（`sales_check` 含 4 维 + `deal_intent` + `next_action`）

### 非目标（YAGNI）

- 不做向量 RAG——结构化 JSON，按主题切片更准
- 不改列表/卡片流程——Q&A 是卡片后的延续
- 不给卡片加问答按钮——保持 text_notice 无回调设计
- 不持久化跨会话缓存——/tmp 会话级即可，跨会话重取刷新 updated_at
