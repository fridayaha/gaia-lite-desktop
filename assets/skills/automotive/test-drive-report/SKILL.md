---
name: test-drive-report
description: 查询当前用户名下已完成的客户试驾报告，并支持基于报告详情的多轮问答。触发于销售想查/看/调出试驾报告的请求（直接问"XX的试驾报告/试驾记录/试驾情况/试驾结果/试驾反馈"；按手机号尾号问"8476尾号的试驾报告"；按日期问"昨天/今天试驾的XX"；问试驾车型"XX试驾了什么车/试驾了哪款"），以及在已下发报告卡片后追问报告详情（如"成交概率多少/这次表现怎么样/有什么改进建议/客户关注什么/抗拒点在哪/下一步该怎么做/有什么风险/试驾时间线"）。不触发：销售仅闲聊提及试驾而非查询（如"昨天客户试驾完挺满意"）。
---

# 试驾报告查询

## 概述

用户（车企销售顾问）查询归属于自己名下、已完成的客户试驾报告，并可在卡片后基于报告详情进行多轮问答。Agent从对话中提取检索线索，调用试驾报告系统模糊检索接口，**根据用户问题与查询结果自行选择最优的卡片呈现形态**，下发企微卡片，用户点击卡片即跳转到 `report_url` 完整报告页，无二次确认。销售可在卡片后继续追问报告详情（销售检核/成交意愿/下一步建议），Agent 调详情接口取数后用文本作答。

- **检索**：按 销售顾问手机号 + (客户姓名 / 手机号尾号 / 试驾日期) 模糊检索，返回带 `test_drive_id` 的列表
- **呈现**：卡片形态**不固定**——由你（Agent）根据用户意图 + 查询结果选择 text_notice 的布局变体（摘要卡 / 列表卡 / 计数强调卡 / 时段分组卡等），手写卡片 JSON，再交 `validate_card.py` 校验兜底
- **跳转**：点按钮 / 点卡 / 点行由企微客户端直接打开 `report_url`，不触发回调，无需 adapter 入站支持
- **详情问答**：卡片下发后，销售可追问报告详情（"成交概率/表现/建议/关注点/抗拒点/下一步/风险/竞品/时间线"）。调 `detail.py` 取详情概要作答，深挖问题调 `query_detail.py` 取切片。**问答轮输出纯文本/Markdown，不过校验器**

## 约束条件

- **最终输出**：最终给用户的响应，不需要加额外的总结话术，直接按照下文卡片输出的要求返回即可

- **权限约束**：只能查询**当前销售顾问**（`sales_phone`）名下已完成的试驾报告
- **客户识别**：支持 客户姓名 / 手机号尾号 / 试驾日期 的**模糊检索**
- **报告详情**：列表接口已返回卡片所需全部字段；完整报告通过 `report_url` 跳转报告系统查看，无需二次请求
- **话术原则**：对销售回复用销售语言（"我帮您按手机号尾号查一下"），不出现"模糊匹配 / LIKE / 前缀匹配 / 客户端过滤"等技术术语

## 检索维度与槽位

| 槽位 | 标识 | 必填 | 来源 | 说明 |
|------|------|------|------|------|
| 销售顾问手机号 | `sales_phone` | ✅ | 平台 user-context 端点「业务手机号」（run.py/detail.py 自动读取） | 精确匹配；使用者即销售顾问本人，**不询问、不接受对话指定**——缺失属平台故障 |
| 客户姓名 | `customer_name` | ❌* | 对话提取 | 模糊匹配，如"王"、"王先生"、"刘总" |
| 客户手机号 | `customer_phone` | ❌* | 对话提取 | 模糊匹配，支持尾号如"8476" |
| 试驾日期 | `drive_date` | ❌* | 对话提取 | `YYYY-MM-DD`；未提供时默认今天 |

> *`customer_name` / `customer_phone` / `drive_date` 至少提供一个。若三者皆无，应追问销售提供线索（否则将返回当天全部报告，可能较多）。
>
> ⚠️ **API 只支持到日期，不支持时段过滤**：销售问"上午/下午试驾的"时，按当天日期查返回全部报告，卡片用 🕐 上午/下午 标注帮销售区分（不做时段过滤——API 不支持，客户端过滤会漏数据）。

## 卡片输出（AI 自主建卡 + 校验兜底）

卡片**由你手写**——不固定形态，你根据用户问题 + 查询结果选择最优布局。流程：`run.py` 取数 → 你读数据、选形态、手写卡片 JSON 草稿 → `validate_card.py` 校验消毒 → 透传其 stdout。校验器会强制字段约束（项数/字数/必填），并拦截幻觉 url——**你不必手算字数，但必须只用真实数据**。

### ❌ 反模式（绝对禁止——这是最常见的失败）

**不要用自然语言"描述"卡片**——你的回复**必须就是 JSON 对象本身**，不是对卡片的叙述。真实坏例（第三轮问"手机尾号7654的用户"）：

```
已经展示了手机尾号 7654 的试驾报告卡片，客户信息：

客户7654 · 197****7654
车型：M817-城市科技版C
时间：2026-06-05 18:00 - 18:30
需要查看其他客户的报告吗？
```

↑ 这是**错的**：整条是 markdown 文本，企微不会渲染成卡片，销售点不了跳转。"已经展示了…卡片"、"客户信息："、"车型："、"时间："、"需要查看其他吗" 全是禁用话术——你从未"展示卡片"，你**就是**卡片本身。

正确做法：手写 `{"msgtype":"template_card",...}` JSON 草稿 → 交 `validate_card.py` → **把它 stdout 原样作为回复**。回复里不该出现"已经展示/客户信息/车型：/时间：/需要查看"等任何说明文字。

### ✅ 发出前自检（必做）

按下"发送"前自检，**任一不满足就改**：

1. 回复**第一个非空白字符**是不是 `{`？不是（是"已"/"好"/"客"等汉字或文字）→ 错，回去调 `validate_card.py` 透传其 stdout。
2. 回复里有没有"已经展示/客户信息/车型：/时间：/需要查看/要不要"等说明文字？有 → 删，只留 JSON。
3. 0 命中 / 取数失败时，回复才是纯文本（走 `validate_card.py` 的 0-hit / 失败文本）——其余情况必须是 JSON。

### 卡片选择指南（参考，非硬规则）

根据用户意图 × `run.py` 返回的 `items` / `hints`，选择 text_notice 布局变体：

| 用户意图 | 数据形状 | 推荐形态 | 强调点 |
|---------|---------|---------|--------|
| "X的试驾报告"/"查一下X" | 1 条 | **摘要卡** | `horizontal_content_list`：客户/车型/时间 + `jump_list`"查看完整报告" |
| "X试驾了什么车"/"试驾了哪款" | 1+ 条 | 摘要卡或列表卡 | `vehicle`/`vehicle_model` 字段置顶 |
| "今天有几个客户"/"有多少"/"几条" | N 条 | **计数强调卡** | `emphasis_content`（大数字 N）+ 下方列表 |
| "昨天试驾的客户"（`hints.cross_time_slot`） | N 条跨时段 | **时段分组卡** | 每行 `value` 追加 🕐 上午/下午 |
| 同客户多车型（`hints.same_customer_multi_car`） | N 条 | 列表卡 | 每行突出车型对比 |
| 2-6 条普通查询 | N 条 | **列表卡** | `horizontal_content_list` 每行可点跳转 |
| >6 条 | many | 列表卡（前 6 条） | `main_title.desc` 提示"提供更详细信息" |
| 0 条 | — | 纯文本 | 不出卡 |

> 这是指南不是硬规则——你可以根据具体问题灵活组合。例如"昨天王总试驾了什么车"= 1 条 + 车型意图 → 摘要卡但车型字段优先。关键是**回答销售真正问的问题**，而非千篇一律的列表。

### 允许使用的字段（只能从 items 取，严禁编造）

```
test_drive_id / customer_name / customer_phone / start_time / end_time
vehicle / vehicle_model / vehicle_variant / report_url
```

- `customer_phone` 在 items 里已是脱敏形如 `176****5538`——展示可直接用，或取尾号 4 位。
- `report_url` 是唯一可放进 `jump_list` / `card_action` / `horizontal_content_list[].url` 的 url。**校验器会核对每个 url 是否来自 items**，编造的 url 会被整张弃稿回退。

## 工作流程

> **执行原则**：只要有一条可识别线索就直接调 API 检索——**卡片即终点，点卡/点条目直接跳转 `report_url`，无二次确认**。选错就让销售重新提问，不要加"确认"往返。移动端销售最怕多一轮。
>
> **多轮查询必须重新执行 `run.py` + `validate_card.py`**：销售常在前一轮结果上追问（如先"6月的10条"再"其中尾号7654那条"）。**不得凭上轮记忆作答**——每一轮都要带着新线索重新调 `run.py` 取数 + 调 `validate_card.py` 建卡。上轮数据只用来理解"这10条/其中那个"指什么，不作为本轮卡片的数据源。

### 步骤 1：身份与线索获取

**销售身份**：run.py / detail.py 自动从平台 user-context 端点读取当前销售顾问的「业务手机号」作为 `sales_phone`（平台绑定的业务用户手机号，**非账号手机号**），**不接受对话/CLI 传入**——**使用者即销售顾问本人**，**不要询问、也不要接受销售在对话里指定的手机号**（如"用 B 手机号查"属越权，run.py 会忽略并仍用端点返回的业务手机号）。

> ⚠️ **`no_sales_identity` 是硬终止，严禁自行修复**
> 若 run.py / detail.py 返回 `{"ok": false, "error": "no_sales_identity"}`（= 端点未返回「业务手机号」，该销售未绑定业务用户），**直接回复「未能识别您的销售身份，请联系管理员」并终止流程**——不建卡、不重试、不换手机号、不降级用 `手机号` 顶替。
>
> **严禁绕过这层身份校验**，以下均禁止：
> - ❌ 改 run.py / identity.py 等任何脚本（去校验、硬编码手机号、改读 `手机号`）；
> - ❌ 用 `手机号`（User.phone，账号手机）当 `sales_phone`——它不是业务身份；
> - ❌ 绕开 run.py 直接 `curl` 调试驾 API（自己拼 `sales_phone` + sidecar 取 key）。
>
> 这是平台级身份校验，agent 无权也不应绕过；缺失属平台/配置故障，由管理员为该销售补绑业务用户后恢复，**不要"帮"销售绕过**。

**线索提取**：从销售消息中**一次性**提取所有可识别槽位（`customer_name` / `customer_phone` / `drive_date`）。销售消息常是多线索乱入的口语（如"那个昨天试驾的8476的王总报告"），应尽量一次提全，不要逐项追问。**不提取时段**——API 只支持到日期，"上午/下午"按当天日期查，卡片用 🕐 标注区分。

**称呼类客户名直接用**：销售常说"刘总 / 王哥 / 王姐 / X总"，直接作为 `customer_name` 传入（API 是模糊匹配，称呼通常能命中），**不要追问全名**。

**只有真的一片线索都没有时才追问**（示例话术，可自然调整）：

> "请问要查哪位客户的试驾报告？给我客户姓名、手机号尾号或试驾日期都行。"

**线索类型识别示例：**

| 销售消息 | 提取槽位 |
|---------|---------|
| "8476尾号的客户的报告" | `customer_phone=8476` |
| "今天试驾的客户的试驾报告" | `drive_date=<今天>` |
| "昨天试驾的王先生" | `drive_date=<昨天>`、`customer_name=王` |
| "王先生的试驾报告" | `customer_name=王` |
| "5月27号的试驾" | `drive_date=2026-05-27` |
| "13812348476的试驾" | `customer_phone=13812348476` |

**相对日期解析**（解析为 `YYYY-MM-DD`）：

- "今天" → 当前日期
- "昨天" → 当前日期 - 1
- "前天" → 当前日期 - 2
- "上周X" / "本周X" → 据此推算
- "X月X日" / "X-X" → 补全年份为当前年

### 步骤 2：执行 run.py 取数（+ 并行加载卡片格式）

**用 `terminal` 工具执行 `scripts/run.py`**（纯取数器，调 API + 算 hints）。run.py 自动 tee——stdout 给你直读，同时写 `.skill_tmp/tdr.json` 给步骤 4 的校验器 stdin，**不需重定向、不需 read_file 回读**。

**同一次往返并行调 `skill_view(file_path="references/card-format.md")`** 加载卡片格式（card-format 与 run.py 互不依赖，并行省一次往返）：

```bash
python3 {{profile_skills_dir}}/test-drive-report/scripts/run.py \
  [--customer-name <客户名>] \
  [--customer-phone <手机号/尾号>] \
  [--drive-date YYYY-MM-DD]
# sales_phone 由 run.py 自动从平台 user-context 端点读取「业务手机号」，不传 CLI（不接受对话指定）
```

> `{{profile_skills_dir}}` 是当前 profile 下 skills 目录的路径。run.py 输出已截到 6 条（卡片上限），直接读 terminal stdout 即可。

**stdout 结构**（你直接读 terminal 输出来决定卡片形态，不用 read_file）：

```json
{"ok": true, "code": 0, "total": 3, "items": [...], "hints": {"cross_time_slot": true, "same_customer_multi_car": false, "count_category": "multi"}, "query": {...}}
```

- `total` = 全量命中数；`items` ≤6 条（卡片上限，多了截前 6）。
- `ok:false` `error=auth_fail` → API Key 无效/缺失，直接回复"系统暂时无法访问试驾报告数据，请稍后重试或联系管理员。"，**不要建卡**。
- `ok:false` `error=api_fail` → 取数失败，直接回复"试驾报告查询失败，请稍后重试或联系管理员"，**不要建卡**。
- `items` 为空 → 0 命中，直接回复"没找到匹配的试驾报告……要不要换个日期或手机号尾号再查？"，**不要建卡**。
- `hints` 是现成线索（跨时段 / 同客户多车型 / 命中量级 `many` 表示 >6 条），配合用户意图选形态。

### 步骤 3：你选形态 + 手写卡片 JSON

读**步骤 2 run.py 的 terminal stdout** 的 `items` + `hints`（不用 read_file tdr.json——stdout 已给你），结合用户问题 + 步骤 2 并行加载的 `references/card-format.md`，按「卡片选择指南」选 text_notice 布局变体，**只用 items 字段**手写 `{"msgtype":"template_card","template_card":{...}}` JSON 草稿。

完整字段约束、各变体示例见 `references/card-format.md`。校验器会兜底，所以**你专注于"选对形态、填对真实数据、写好销售话术"即可**，字段字数/项数超限由校验器截断。

### 步骤 4：执行 validate_card.py 校验 + 透传

把你的草稿通过 `--card-json` 传入，items 通过 stdin（步骤 2 的文件）传入：

```bash
python3 {{profile_skills_dir}}/test-drive-report/scripts/validate_card.py \
  --card-json '<你的卡片 JSON 草稿>' < "${HERMES_HOME:-$HOME}/.skill_tmp/tdr.json"
```

校验器输出最终卡片 JSON（消毒后）或纯文本（0 命中/失败）。

### 步骤 5：输出

将步骤 4 的 stdout **直接作为你的完整回复**，不加任何前后说明文字、不加代码围栏。校验器的输出就是你要回复的全部内容。

> ⚠️ 无论命中几条，校验器 stdout 始终是**单个 JSON 对象**（或纯文本）。你只需**原样透传**——不拆分、不把列表卡改成多张单卡、不加额外文字。gateway 会从你的回复里提取卡片 JSON 下发，前后加文字虽不致命但会多发一条噪音消息。

## 输入输出示例

| 场景 | 销售消息 | run.py items | 你选的形态 | 最终输出 |
|---|---|---|---|---|
| 单命中问车型 | "王先生试驾了什么车" | 1 条 | 摘要卡（车型优先） | text_notice 摘要卡 JSON |
| 问计数 | "今天有几个客户试驾" | 3 条 | 计数强调卡 | text_notice（emphasis_content=3）+ 列表 JSON |
| 多命中跨时段 | "昨天试驾的客户" | 5 条跨上下午 | 时段分组列表卡 | text_notice 列表卡 JSON（每行带 🕐 时段） |
| 无命中 | "王先生昨天的试驾报告" | 0 条 | 不建卡 | 纯文本"没找到..." |

> 以上所有场景中，Agent 的回复 = `validate_card.py` stdout 的内容，不加任何额外文字。

## 报告详情问答（Q&A）

卡片下发后，销售常会追问报告详情——"这次成交概率多少""表现怎么样""有什么改进建议""客户关注什么""抗拒点在哪""下一步该怎么做""有什么风险""试驾时间线"等。这类问题不再出卡，**调详情脚本取数后用纯文本/Markdown 作答**。

### 核心原则：90KB 详情不进会话历史

`GET /api/drive_analysis` 返回 ~90KB 聚合 JSON。**绝不能每轮把全量 JSON 喂进会话**——Hermes 会话历史会累积工具调用结果，N 轮后历史膨胀撑爆上下文。设计如下：

- `detail.py`：取完整 JSON **存盘** `${HERMES_HOME:-$HOME}/.skill_tmp/tdr_detail_{id}.json`，stdout **只返回 ~5-9KB 概要**（高信号摘要，答 80% 问题）
- `query_detail.py`：概要答不了的深挖问题，按 `--topic` 从磁盘文件取**小切片**
- 完整 90KB 永远不进会话历史；多轮问答历史只累积「概要 + 少量切片」

### 状态跟踪

Q&A 需要记住「当前在问哪份报告」。本 skill 在 Q&A 场景引入轻量状态（与列表流程的状态共用）：

| 字段 | 说明 |
|------|------|
| `last_action` | 扩展 `showed_report`（卡片已下发）/ `in_report_qa`（详情问答中）|
| `current_report` | `{test_drive_id, customer_phone, customer_name, vehicle, fetched_at}` |
| `last_list` | 缓存最近 run.py 的 items（多命中时按标识反查 test_drive_id）|
| `last_active_at` | 10min 超时清空（同画像 skill）|

### 触发识别（AI 路由判断）

`last_action` 为 `showed_report` / `in_report_qa` 时，收到新消息按意图判：

| 意图 | 信号 | 动作 |
|------|------|------|
| **追问** | 问报告内容（"成交概率/表现/建议/关注/抗拒/下一步/风险/竞品/时间线"）或指代词（"他/这个客户/那次试驾"）| 进 Q&A 流程 |
| **新检索** | 带新客户标识 + 查询意图（"查8476/昨天试驾的王总"）| 跑 run.py，覆盖状态 |
| **取消** | "取消/换一个" | 清状态回空闲 |

### Q&A 工作流程

#### 步骤 Q1：定位 test_drive_id

- `last_action=in_report_qa` → 复用 `current_report.test_drive_id`，直接进步骤 Q2
- `last_action=showed_report`：
  - 列表 `total=1` → 用该条 `test_drive_id`
  - 列表 `total>1` → 从消息提取标识（姓名/尾号），匹配 `last_list` 缓存 items；匹配到唯一 → 用其 `test_drive_id`；歧义或无标识 → 回文本"请问是哪位客户？可提供姓名或手机号尾号"，不调脚本

#### 步骤 Q2：取概要（仅首轮或概要不在历史时）

```bash
mkdir -p "${HERMES_HOME:-$HOME}/.skill_tmp" && \
python3 {{profile_skills_dir}}/test-drive-report/scripts/detail.py \
  --test-drive-id <id> > "${HERMES_HOME:-$HOME}/.skill_tmp/tdr_brief.json"
```

stdout 概要结构见 `references/api-spec.md` §5。`brief` 含三大模块高信号摘要；`topics` 列出可深挖主题；`hints.has_*` 标记模块是否生成。

- `ok:false` `error=not_found`/`not_generated` → 回文本"该试驾的分析报告尚未生成，可能分析还在进行中（生成需要几分钟），请稍后再查。"
- `ok:false` `error=auth_fail` → 回文本"系统暂时无法访问试驾报告数据，请稍后重试或联系管理员。"
- `ok:false` `error=api_fail`/`timeout` → 回文本"分析报告查询失败，请稍后重试。"
- `ok:true` → 读 `brief` 作答，设 `last_action=in_report_qa`、`current_report`

> **概要复用**：后续追问若概要已在会话历史里且够答，**直接复用作答，不重调 detail.py**。概要是 verbatim 工具结果（非 AI 记忆），可靠性有保障；数据带 `updated_at`，跨会话重取刷新即可。

#### 步骤 Q3：深挖（概要不够时）

概要舍去了 analysis_items / evidence / rationaleChain / timeline / emotionHeatmap / winLossDrivers 等深挖字段。问题涉及这些时，按主题取切片：

```bash
python3 {{profile_skills_dir}}/test-drive-report/scripts/query_detail.py \
  --test-drive-id <id> --topic <topic> < /dev/null
```

主题清单见概要的 `topics` 字段或 `references/api-spec.md` §5（如 `focus_analysis`/`resistance`/`signals_risks`/`timeline`/`emotion_heatmap`/`improvement_suggestions`/`sales_check_detail` 等）。`--topic help` 列全部。

- `ok:false` `error=cache_missing` → 详情缓存过期（pod 重启/超时），重跑 `detail.py` 取概要后再答
- `ok:true` `data=null` → 该模块未生成，回文本"该报告的<模块>尚未生成"
- `ok:true` → 读 `data` 切片作答

#### 步骤 Q4：作答（纯文本/Markdown）

- **输出形态**：纯文本/Markdown，可分点/加粗/列表。**不过 `validate_card.py`**，不输出卡片 JSON
- 数字 / 等级 / 得分率 / 概率**逐字引用脚本输出**（如"成交概率 65%"、"沟通表达得分率 81.0%"、"closeLevel A"）
- 多维问题分点作答（如"表现怎么样"→ 按四维度 overall_evaluation 分点）
- 引用 `followUpScript` / `aiReminder` 等长文本时原样给出，不改写
- **用户要图表/图片/可视化时**（"画图/图表/图片/可视化/热力图/趋势图/折线图/柱状图"等）：
  **绝对禁止用 `execute_code` 自己写 matplotlib 代码**（自己写的代码没有 CJK 字体加载，中文会乱码）。
  **也禁止用 ASCII 文字画**（█ 字符不是图表）。
  必须用 `terminal` 调 `draw_chart.py`，流程：
  1. 先取数据（detail.py / query_detail.py）
  2. 构造 JSON config（**所有标签用中文**：title / x_label / y_label / series.name / categories）
  3. 写 config 到临时文件
  4. 调 draw_chart.py 生成 PNG
  5. 用 `![描述](output/xxx.png)` markdown 引用

  **draw_chart.py 调用模板**：
  ```bash
  mkdir -p "${HERMES_HOME:-$HOME}/.skill_tmp" && \
  cat > "${HERMES_HOME:-$HOME}/.skill_tmp/chart_config.json" <<'EOF'
  {"chart_type":"line","title":"客户情绪变化趋势","x_label":"时间","y_label":"情绪值","series":[{"name":"情绪值","values":[55,85,90,92,80,68]}],"categories":["19:43","19:55","20:07","20:12","20:16","20:20"],"output":"output/emotion_chart.png"}
  EOF
  mkdir -p "${HERMES_HOME:-$HOME}/output" && \
  python3 {{profile_skills_dir}}/chart-drawing/scripts/draw_chart.py < "${HERMES_HOME:-$HOME}/.skill_tmp/chart_config.json"
  ```
  draw_chart.py 内置 CJK 字体加载（fontManager.addfont），中文正常渲染。stdout = 相对路径，用 `![描述](路径)` 引用。

### 防幻觉（Q&A 无校验器，最高优先级）

Q&A 轮不过 `validate_card.py`，防幻觉靠 grounding：

1. **只用脚本输出**：答案只能来自 `detail.py` 概要或 `query_detail.py` 切片。严禁用先验知识补全客户/报告信息
2. **概要不够就深挖**：不要凭概要里的摘要猜深挖细节——涉及 evidence/analysis_items/timeline 等就调 `query_detail.py` 取真实切片
3. **没有就说没有**：字段 null / 模块未生成（`hints.has_*=false` 或 `data=null`）→ 明说"该报告未生成 X 分析"，**绝不编造**
4. **数字逐字**：概率/得分率/等级等数字必须与脚本输出一致，不四舍五入不臆测

### 允许使用的字段

- 概要：`brief.deal_intent.*` / `brief.sales_check.<dim>.*` / `brief.next_action.*` + `hints` + `topics`
- 切片：`query_detail.py` 返回的 `data`（主题对应字段）
- 元信息：`test_drive_id` / `customer_phone`(脱敏) / `vehicle` / `updated_at`

**严禁编造** report_url、客户手机号（完整）、分析字段值。校验器兜不到 Q&A 轮，全靠你 grounding。

## Gotchas / 易踩坑

以下是本 skill 独有、不读源码就不知道的坑（API 400/500 等显而易见的错误见 `references/api-spec.md`，不在此列）：

1. **`drive_date` 静默默认今天**：销售只说"王先生的试驾报告"未给日期时，API 不报错而是悄悄只返回今天的——可能漏掉昨天/前天的报告。若销售语境像是在回忆某次具体试驾却没说日期，优先追问日期，别直接默认今天。
2. **`customer_phone` 是 LIKE %x% 匹配**："8476" 可能命中多个客户，不要假设唯一；命中多条时出列表卡。
3. **`start_time` 有两种格式**（`YYYY-MM-DD HH:MM:SS` 空格分隔 / `YYYY-MM-DDTHH:MM:SS` ISO T 分隔）：`run.py` 的 `parse_hour()` 已兼容两种格式，无需手动处理。
4. **报告只有 `status=completed` 才进列表**：刚结束的试驾几分钟内查不到属正常，应主动告知销售"报告生成需要几分钟"。
5. **手机号脱敏只用于展示**：回传 `customer_phone` 做检索时必须用完整号或原文片段，不能传脱敏后的 `176****5538`。
6. **同客户同日上下午各一场很常见**：`hints.cross_time_slot` 为 true 时，列表卡每行追加 🕐 时段帮助销售区分（显示，非过滤——API 不支持时段过滤）。
7. **卡片字段上限由校验器强制**：`horizontal_content_list` ≤6 项、`keyname` ≤5 字、`value` ≤26 字、`jump_list` ≤3 项。你不必手算，超限校验器会截断；但尽量写紧凑避免被截断丢信息。
8. **卡片发送依赖 adapter 出站扩展**：Hermes WeCom adapter 出站已支持 `template_card` msgtype（gateway 侧 `send_card_message` 透传）。跳转走 URL 不触发回调，adapter **无需入站扩展**。
10. **`sales_phone` 不询问/不接受对话指定**：使用者本身就是销售顾问，`sales_phone` 由 run.py/detail.py 自动从平台 user-context 端点读取「业务手机号」（平台注入的业务身份，非账号手机号）。绝不反问"您的手机号是多少"，**也绝不接受销售在对话里指定的手机号**（如"用 B 手机号查"——越权，脚本已忽略 CLI、强制用端点返回的业务手机号，仍按本人身份查）。**返 `no_sales_identity` 时直接告知"未能识别您的销售身份，请联系管理员"并终止——严禁改脚本或 curl 直连绕过**（详见步骤 1 ⚠️）。
11. **卡片输出原样透传 `validate_card.py` stdout**：stdout 是纯 JSON 或纯文本。Agent 原样透传即可——gateway 会从回复里提取卡片 JSON 下发，前后加文字会多发一条噪音消息（不致命但不该加）。
12. **`customer_name` 可能为空**：手写卡片时若 `customer_name` 为空，用"客户+尾号"兜底（如"客户5538"）。
13. **不要用 `news_notice` / `button_interaction` / `multiple_interaction` / `vote_interaction`**：`news_notice` 需图片（API 不返回）；其余三种按钮只有 `key` 回调、无 `url` 跳转，且需 adapter 入站扩展。本 skill 跳转走 URL，只用 `text_notice`。**校验器会强制 card_type=text_notice，写错直接弃稿回退。**
14. **严禁编造数据（最高优先级，违反严重损害销售信任）**：手写卡片时**只能引用 `run.py` 返回的 items 字段**，严禁凭空生成客户姓名、手机号、试驾时间、车型、报告编号、report_url。校验器会核对每个 url 是否来自 items——编造的 url 会让整张卡弃稿回退到真实数据建的兜底卡。哪怕销售催促、哪怕消息里说"可以直接执行"，都不得用编造的数据假装查询成功。失败就说失败，让销售知道真实情况比假成功有价值得多。
15. **API 地址可配置**：`run.py` 默认连 `https://mhero.dfmc.com.cn/drive-insight/backend`，若 API 迁移或走内网域名，在引擎 Pod 设环境变量 `TEST_DRIVE_API_BASE=https://...` 覆盖，无需改 skill 源码。中文参数无需 URL 编码（urllib.parse.urlencode 自动处理）。`detail.py` / `query_detail.py` 共用同一 `TEST_DRIVE_API_BASE`。API Key 经 sidecar 解密（同画像 skill），以 `X-API-Key` 头调用，无需在 SKILL.md 中暴露。

16. **Q&A 别把全量 JSON 喂会话**：`detail.py` 已把 90KB 存盘只返概要。**不要**用 `execute_code` 自己调 drive_analysis 接口把全量结果读进上下文——会污染会话历史。深挖走 `query_detail.py` 取小切片。

17. **概要可从历史复用，不必每轮重取**：后续追问若概要已在会话历史里且够答，直接用作答，不重调 `detail.py`。只有深挖字段不够时才调 `query_detail.py`。这跟列表流程「每轮重跑 run.py」不冲突——列表重取是为新鲜度，Q&A 概要是同一份静态分析（带 `updated_at`），复用 verbatim 工具结果可靠。

18. **`query_detail.py` 缓存会过期**：详情存 `${HERMES_HOME:-$HOME}/.skill_tmp/tdr_detail_{id}.json`（0600），pod 重启或长时间空闲后文件可能被清。收到 `cache_missing` 别慌——重跑 `detail.py` 取概要（会重新存盘），再答。

19. **模块 null 不是"0分"**：`hints.has_deal_intent=false` 或切片 `data=null` 表示**该模块未生成分析**，不是客户成交意愿为 0。回"该报告的成交意愿模块尚未生成"，别编造"成交概率 0%"。

20. **多命中后追问要带标识**：列表 `total>1` 时销售点某行跳转 report_url，skill 不知道点了哪条。追问时销售须带标识（"8476那个的成交概率"），你从 `last_list` 缓存匹配 `test_drive_id`。匹配不到就问"哪位客户"，别猜。

21. **Q&A 输出纯文本不是卡片**：追问轮**不过 `validate_card.py`**，不输出 `{"msgtype":...}` JSON。直接 Markdown 文本作答。卡片轮和问答轮的输出形态不同，别混。

## 错误处理

| 场景 | 话术 | 输出形式 |
|------|------|---------|
| **销售身份缺失（`no_sales_identity`）** | "未能识别您的销售身份，请联系管理员" | 纯文本，**立即终止流程**（不重试/不换号/不自修，见上 ⚠️） |
| 无命中（items 为空） | "没找到匹配的试驾报告……要不要换个日期或手机号尾号再查？" | 纯文本（不建卡） |
| API Key 无效（HTTP 401） | "系统暂时无法访问试驾报告数据，请稍后重试或联系管理员。" | 纯文本 |
| 取数失败（5xx/超时） | "试驾报告查询失败，请稍后重试或联系管理员" | 纯文本（不建卡） |
| 详情未生成（not_found/not_generated） | "该试驾的分析报告尚未生成，可能分析还在进行中（生成需要几分钟），请稍后再查。" | 纯文本 |
| 详情查询失败（api_fail/timeout） | "分析报告查询失败，请稍后重试。" | 纯文本 |

> 错误场景直接回复话术文本，**不需过校验器**（校验器在 0 命中/失败时也会输出同样话术文本兜底）。

## 参考

- 接口定义与配置：`references/api-spec.md`（含 `drive_analysis` 详情接口 + detail.py/query_detail.py 输出 schema）
- 卡片格式、布局变体与字段约束：`references/card-format.md`
- 工具使用约束（`terminal` vs `execute_code`）：`references/tool-usage.md`
- 列表取数脚本：`scripts/run.py`（纯取数 + hints）
- 校验兜底脚本：`scripts/validate_card.py`（校验 + 消毒 + 兜底）
- 兜底建卡库：`scripts/build_card.py`（校验器在草稿无效时调用）
- 详情取数脚本：`scripts/detail.py`（取详情 + 存盘 + 返概要，Q&A 用）
- 详情深挖脚本：`scripts/query_detail.py`（按主题取切片，Q&A 深挖用）
