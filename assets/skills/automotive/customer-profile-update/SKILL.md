---
name: customer-profile-update
description: 当用户（车企销售顾问）请求查看或更新客户画像时使用。触发场景：销售说"查/看/调出XX画像""更新XX画像""XX的客户资料"，或收到【企微卡片按钮点击】回调消息（key 为 select_ / page_next / page_prev / cancel 之一）时——本技能是这类按钮回调的唯一处理者，其他技能不应拦截。也支持在画像卡下发后追问画像详情（"成交概率多少/客户类型/情绪状态/有什么标签/用车场景/动机/抗拒点/雷达图/突破点/推理依据"）。不触发：纯闲聊、查电话号码本身、未提及具体客户。
---

# 客户画像查询与更新

销售人员（用户）通过企业微信查询终端客户（客户）的画像。查询和更新路径一致：模糊检索 → 选择客户 → 展示画像卡。查看完整画像通过画像卡上的「查看完整画像」链接或点击卡片跳转外部系统，不在企微内处理上传。

> 角色：用户 = 车企销售顾问；客户 = 买车的终端客户。

## 约束

- 只能查询**归属于当前销售**的客户（后端基于 API Key + `sales_rep` 过滤）
- 支持手机号片段 / 客户名称的**模糊检索**

## 输出格式（最高优先级，违反则 skill 失败）

**最终响应体必须是纯 JSON，从 `{` 开始到 `}` 结束，前后不能有任何字符。**

- ✅ `{"msgtype":"template_card",...}` → Gateway 识别为卡片并渲染
- ❌ ```json\n{...}\n``` + 文本说明 → Gateway 降级为纯文本，卡片不渲染
- ❌ `{...}` + 后附文本摘要/说明/建议 → 同上降级
- 所有话术（问候/确认/提示/总结/说明/建议）必须写进卡片字段（`main_title` / `sub_title_text` / `horizontal_content_list`）
- 无论 CLI 还是 Gateway 环境，都只输出纯 JSON，**不追加任何解释性文本、说明、建议**
- 如果想补充说明，把它放进 `sub_title_text` 或新增 `horizontal_content_list` 行

### ❌ 反模式（绝对禁止——这是最常见的失败）

**不要用自然语言"描述"卡片**——你的回复**必须就是 JSON 对象本身**，不是对卡片的叙述。真实坏例：

```
已经展示了客户5678的画像，客户信息：
整体标签：务实家用型决策者
意向车型：追光
预算区间：30-40万
需要查看其他客户吗？
```

↑ 这是**错的**：整条是 markdown 文本，企微不会渲染成卡片，销售点不了「查看完整画像」链接。"已经展示了…画像"、"客户信息："、"整体标签："、"需要查看" 全是禁用话术——你从未"展示卡片"，你**就是**卡片本身。

正确做法：手写 `{"msgtype":"template_card",...}` JSON 草稿 → 交 `validate_card.py` → **把它 stdout 原样作为回复**。

### ✅ 发出前自检（必做）

1. 回复**第一个非空白字符**是不是 `{`？不是 → 错，回去调 `validate_card.py` 透传其 stdout。
2. 回复里有没有"已经展示/客户信息/整体标签：/意向车型：/需要查看/要不要"等说明文字？有 → 删，只留 JSON。
3. 取数失败 / 0 命中 / 无画像时，回复才是纯文本（走 `validate_card.py` 的话术文本）——其余情况必须是 JSON。

## 工具使用

**用 `terminal` 执行固定脚本**，不用 `execute_code` 现写 Python。固定脚本确定性高、API 调用与枚举映射逻辑固化、且校验器能兜底幻觉。脚本只用标准库（urllib），无第三方依赖。

四脚本分工：
- `scripts/search.py` — 模糊检索取数 → 结构化 `{ok,total,items,query,hints}`
- `scripts/profile.py` — 取完整画像 + 枚举映射 → 干净 `{ok,fields,update_url,...}`
- `scripts/validate_card.py` — 校验 AI 草稿 + 消毒 + 兜底
- `scripts/build_card.py` — 兜底建卡库（草稿无效时由校验器调用）

**中间文件目录（多租户隔离）**：所有脚本 stdout 重定向的中间文件（`cp.json` / `cp_brief.json`）和详情缓存统一写到本 profile 私有目录 `${HERMES_HOME:-$HOME}/.skill_tmp/`（0700，随用户删除自动清理，多租户互不可见）。**写文件前先 `mkdir -p "${HERMES_HOME:-$HOME}/.skill_tmp"`**；读文件直接用同一路径。不要写到共享 `/tmp`（会跨租户碰撞 / 越权读旧数据）。

## 会话状态跟踪

轻量状态跟踪，不用正式状态机。状态由 skill 内部维护。

| 字段 | 说明 |
|------|------|
| `last_action` | `searched_list` / `showed_profile` / `null` |
| `current_customer` | `{customer_id, phone, name, level}` |
| `search_result` | `{items, total, search_params}`（翻页和 select_ 反查用，缓存 search.py 输出） |
| `current_page` | 当前页码（1-based，每页 5 条） |
| `last_active_at` | 最后操作时间戳 |

校验：`select_{id}` 需 `last_action==searched_list`；`page_next/page_prev` 需有 `search_result`。超时 10 分钟清空状态。发"取消"清空状态。发新检索线索覆盖状态。

## 工作流程

### 步骤 1：提取检索线索

从消息提取手机号片段 / 手机尾号 / 客户名称。无线索则返回 button_interaction 卡片："请问要查询哪位客户？可提供手机号或姓名。"（带「结束」按钮）——**过 `validate_card.py`** 后输出。更新意图时话术可带"点击画像卡或查看完整画像可跳转画像系统更新"。

> **尾号 vs 片段**（重要）：销售说"尾号8001"→ 用 `--phone-tail 8001`（精确匹配尾号）；销售说手机号片段但非尾号 → 用 `--phone-keyword <片段>`（模糊匹配任意位置）。API 的 `phone_keyword` 是 LIKE %X% 全文模糊，`--phone-tail` 在此基础上客户端过滤 `phone.endswith(X)`，避免中间含 X 的误匹配。

> **跨 skill 复用客户手机号**（重要）：如果会话历史已有该客户的**完整手机号**（如刚查过试驾报告返回的 `customer_phone`，或上一轮画像查询 items 里的 `phone`），**必须优先用 `--phone-keyword <完整手机号>` 精确查**（LIKE 匹配唯一，返回1条直接建卡），**不要**用 `--customer-name-keyword` 模糊查姓名——同名客户可能多个（如"万先生"8位），会返回多条需要选择，体验差。仅当上下文无该客户完整手机号时，才用姓名/尾号查。例：上一轮试驾报告返回"万先生 · 13165656630"，这轮"查万先生画像"→ 用 `--phone-keyword 13165656630`，不要用 `--customer-name-keyword 万先生`。

### 步骤 2：执行 search.py 取数（+ `--fetch-profile` 合并取画像）

**用 `terminal` 工具执行 `scripts/search.py`**。search.py 自动 tee——stdout 给你直读，同时写 `.skill_tmp/cp.json` 给步骤 3/5 的校验器 stdin，**不需重定向、不需 read_file 回读**。

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/search.py \
  --customer-name-keyword <客户名> --fetch-profile
# 或按手机号片段（模糊匹配任意位置）：
python3 {{profile_skills_dir}}/customer-profile-update/scripts/search.py \
  --phone-keyword <手机号片段> --fetch-profile
# 或按手机尾号（精确匹配尾号，如"尾号8001"）：
python3 {{profile_skills_dir}}/customer-profile-update/scripts/search.py \
  --phone-tail <尾号> --fetch-profile
```

stdout 结构（total>1 或取画像失败）：
`{"ok":true,"total":N,"items":[{id,phone,name,deal_level,profile_sync_status,overall_tag}],"query":{},"hints":{"count_category":"none|single|multi"}}`

stdout 结构（total=1 + `--fetch-profile` 成功，**合并画像**）：
`{"ok":true,"total":1,"items":[...],"has_profile":true,"fields":{deal_level,overall_tag,...},"update_url":"...","phone":"...","customer_name":"...","query":{},"hints":{"count_category":"single"}}`

- `ok:false` → 取数失败，按错误表选话术，**不建卡**（直接回复文本，无需校验器）。
- `total=0` → 0 命中，**不建卡**，直接回复"未找到匹配的客户…"文本。
- `total=1` + 输出含 `fields` → **直接跳步骤 5**（画像已合并，跳过步骤 4 的 profile.py）。
- `total=1` + 输出**不含** `fields`（--fetch-profile 取画像失败）→ 跳步骤 4 用该条 `phone` 单独跑 profile.py。
- `total>1` → **必须**缓存 `search_result` + `current_page=1`，进步骤 3 发选择卡。**禁止跳过选择卡直接取画像**（即使只差一个客户、即使觉得能猜中）——销售需要自己选要看哪位客户。
- `items` 输出已截到 30 条（6 页 × 5 条/页）；`total` 保留 API 真值供翻页判断。

### 步骤 3：AI 手写客户选择卡 + 校验（total>1）

读**步骤 2 search.py 的 terminal stdout** 的 `items`（不用 read_file cp.json——stdout 已给你），手写 `button_interaction` 选择卡 JSON 草稿（第 1 页 5 客户 + 「下一页」`page_next`，翻页规则见下方）。**只用 items 真实 id/phone/name**，`select_{id}` 的 id 必须来自 items。然后校验：

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/validate_card.py \
  --card-json '<你的选择卡草稿>' < "${HERMES_HOME:-$HOME}/.skill_tmp/cp.json"
```

校验器核对 `select_{id}` 真实性、`button_list` ≤6/key 唯一，消毒后输出最终卡片 JSON。设 `last_action=searched_list`，**透传 stdout**。

**选择卡翻页规则（`button_list` 上限 6）：**

| 场景 | 按钮布局 |
|------|---------|
| 第 1 页（有更多） | 5 客户 + 「下一页」`page_next` |
| 中间页 | 4 客户 + 「上一页」`page_prev` + 「下一页」`page_next` |
| 最后一页 | 剩余客户 + 「上一页」`page_prev` |
| 总数 ≤5 | 全部客户，无翻页按钮 |

按钮文案只展示「用户名 + 手机号」（`张先生 · 138****8000`），不放标签/级别。**不设「重新搜索」按钮**——销售想换其他客户直接输入文本重新检索。

### 步骤 4：执行 profile.py 取画像 + 枚举映射

> **跳过条件**：如果步骤 2 的 search.py `--fetch-profile` 输出已含 `fields`（total=1 合并成功），**跳过本步骤**，直接进步骤 5 用步骤 2 的输出建画像卡（cp.json 已含 fields + update_url）。

用选中客户（或 total=1 那条）的 `phone` + `name`：

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/profile.py \
  --phone <完整手机号> --customer-name <客户名>
```

profile.py 自动 tee：stdout 给你直读 + 写 `.skill_tmp/cp.json` 给步骤 5 校验器 stdin。**不需重定向、不需 read_file 回读**——直接读 terminal stdout 的 `fields` + `update_url`。

stdout 结构：`{"ok":true,"has_profile":true,"phone":"...","customer_name":"...","update_url":"http://.../customer/{phone}/profile","fields":{deal_level,overall_tag,personality_summary,intended_model,budget_range,current_stage,breakthrough_point,motivations,preferences,resistances},"hints":{}}`

- 枚举映射、`basic_notes` JSON 字符串解析、`||` 多值拆分**已在脚本内完成**，你拿到的是干净 fields。
- `ok:false` → 按错误表选话术文本。
- `has_profile:false` → 回复"暂未找到该客户的画像记录…"文本（不建卡）。
- `has_profile:true` → 进步骤 5。

### 步骤 5：AI 手写画像卡 + 校验

读**步骤 2 或步骤 4 的 terminal stdout** 的 `fields` + `update_url`（不用 read_file cp.json），手写 `text_notice` 画像卡 JSON 草稿（参考试驾报告卡片样式）。**`jump_list` 和 `card_action` 的 url 必须用 stdin 的 `update_url`**（校验器核对，编造 url 整张弃稿）。然后校验：

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/validate_card.py \
  --card-json '<你的画像卡草稿>' < "${HERMES_HOME:-$HOME}/.skill_tmp/cp.json"
```

**画像卡字段（text_notice，参考试驾报告卡片样式）：**
1. `main_title.title` = 客户姓名 + 脱敏手机号（如 `客户5678 · 139****5678`）
2. `main_title.desc` = deal_level + personality_summary（如 `A级意向 · 务实家用型`，截断 20 字）
3. `sub_title_text` = 动机/偏好/抗性 **`\n` 换行**（如 `动机：家庭代步\n偏好：空间/油耗\n抗性：价格`，缺项跳过该行，≤112 字）
4. `horizontal_content_list`（≤5 项，无 url 行）：整体标签 / 意向车型 / 预算区间 / 购买阶段 / 突破策略
5. `jump_list` = `[{"type":1,"url":<update_url>,"title":"查看完整画像"}]`（url 必须 == stdin 的 update_url）
6. `card_action` = `{"type":1,"url":<update_url>}`（必填，url == update_url）
7. **无 `button_list`（无「换一个」）、无 `task_id`、无「更新画像」hcl 行**

`button_interaction` 的 `emphasis_content` / `button_list` 约束不再适用。text_notice 不支持按钮交互，换客户靠重新输入检索。

设 `last_action=showed_profile`，**透传校验器 stdout**。

### 步骤 6：输出

将 `validate_card.py` 的 stdout **直接作为完整回复**，不加任何前后说明文字、不加代码围栏。

## 画像详情问答（Q&A）

画像卡下发后（`last_action=showed_profile`），销售常会追问画像详情——"成交概率多少""客户类型是什么""情绪状态怎么样""有什么推断标签/用车场景""动机/抗拒点的推理依据""雷达图明细""突破点怎么来的"等。这类问题不再出卡，**调详情脚本取数后用纯文本/Markdown 作答**。

### 核心原则：90KB 画像不进会话历史，且画像可变需限定复用

`GET /profile/{phone}` 返回 ~90KB 完整画像（7 模块）。**绝不能每轮把全量喂进会话**。同时画像**可变**（销售可能点「查看完整画像」跳外部系统传素材重生成），与试驾报告（不可变）不同——故概要**不能无限复用**，需 10min TTL 限定：

- `detail.py`：取完整画像 + enum_map **存盘** `${HERMES_HOME:-$HOME}/.skill_tmp/cp_detail_{phone}.json`(0600)，stdout **只返 ~5-9KB 概要**（复用 extract_fields + 扩展 customer_overview/emotion_state/标签/场景）。**每次运行都从 API 现取**（只写缓存、不读缓存服务概要）。
- `query_detail.py`：概要答不了的深挖（reasoning_detail/雷达图/全部 basic_notes），按 `--topic` 从磁盘取**小切片**。读前查 mtime，**超 10min 返 `cache_missing`** → 触发重跑 `detail.py`。
- 完整 90KB 永不进会话历史；多轮问答历史只累积「概要 + 少量切片」。

### 新鲜度三层兜底

1. **TTL 限定复用（10min）**：概要带 `fetched_at`；query_detail 缓存 mtime 超 10min → `cache_missing` → 重跑 detail.py 刷新概要+缓存 → 重跑 query_detail。深挖路径自愈。
2. **`updated_at` + `fetched_at` 可见**：概要带画像 API 的 `updated_at`（若提供）+ 取数 `fetched_at`，销售可见"画像上次更新于 X，取数于 Y"。
3. **显式刷新意图**：销售说"刷新画像/取最新画像/画像是不是最新的" → 无视 TTL 直接重跑 `detail.py`。

> 跨会话由会话超时（10min 空闲清状态）自然解决——新会话首轮必跑 detail.py 现取。

### 状态跟踪

复用现有状态，新增 Q&A 态：

| 字段 | 说明 |
|------|------|
| `last_action` | 扩展 `in_profile_qa`（原有 `searched_list`/`showed_profile`/`null` 不变）|
| `current_customer` | 复用现有 `{customer_id, phone, name, level}`；`phone` 作 detail.py / query_detail.py 缓存 key |

> 画像卡只展示一个客户，Q&A 无多命中歧义——`current_customer.phone` 在 `showed_profile` 后已设值，直接用。

### 触发识别（AI 路由判断）

`last_action ∈ {showed_profile, in_profile_qa}` 时，收到新消息按意图判：

| 意图 | 信号 | 动作 |
|------|------|------|
| **追问** | 问画像内容（"成交概率/客户类型/情绪/标签/场景/动机/抗拒/雷达图/突破点/推理依据"）或指代词（"他/这个客户"）| 进 Q&A 流程 |
| **新检索** | 带新客户标识 + 查询意图（"查 5678 / 王总的画像"）| 跑 search.py，覆盖状态 |
| **取消/换一个** | "取消/换一个" | 清状态回搜索 |
| **刷新画像** | "刷新/取最新/是不是最新的"（`in_profile_qa` 态）| 重跑 detail.py（不换客户）|

### Q&A 工作流程

#### 步骤 Q1：定位 phone

- `last_action=in_profile_qa` → 复用 `current_customer.phone`，直接进步骤 Q2
- `last_action=showed_profile` → 直接用 `current_customer.phone`（画像卡展示后已设）

#### 步骤 Q2：取概要（仅首轮或概要不在历史或已陈旧时）

```bash
mkdir -p "${HERMES_HOME:-$HOME}/.skill_tmp" && \
python3 {{profile_skills_dir}}/customer-profile-update/scripts/detail.py \
  --phone <完整手机号> [--customer-name <客户名>] > "${HERMES_HOME:-$HOME}/.skill_tmp/cp_brief.json"
```

stdout 概要结构见 `references/api-spec.md` §5。`brief` 含三大类高信号摘要（复用 extract_fields + customer_overview + emotion_state + 标签/场景）；`topics` 列可深挖主题；`hints.has_*` 标记模块是否生成。

- `ok:false` `error=auth_fail` → "系统暂时无法访问客户数据，请稍后重试或联系管理员。"
- `ok:false` `error=forbidden` → "该客户可能不归属您，无法查询。"
- `ok:false` `error=api_fail`/`timeout` → "系统繁忙，请稍后重试。"
- `ok:true` `has_profile=false` → "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。"（不进 Q&A）
- `ok:true` `has_profile=true` → 读 `brief` 作答，设 `last_action=in_profile_qa`

> **概要复用**：后续追问若概要已在会话历史里且够答且 `fetched_at` 在 10min 内，**直接复用作答，不重调 detail.py**。若 `fetched_at` 跨小时/隔天，重跑 detail.py 取最新。深挖时 query_detail 返 `cache_missing` 也触发重跑。

#### 步骤 Q3：深挖（概要不够时）

概要舍去了 reasoning_detail / evidence / radar_data 明细 / 全部 basic_notes 等深挖字段。问题涉及这些时，按主题取切片：

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/query_detail.py \
  --phone <完整手机号> --topic <topic>
```

主题清单见概要的 `topics` 字段或 `references/api-spec.md` §5（`basic_notes_detail`/`customer_overview_detail`/`emotion_detail`/`motivations_detail`/`preferences_detail`/`resistances_detail`/`inferred_tags`/`usage_scenarios`/`personality`）。`--topic help` 列全部。

- `ok:false` `error=cache_missing` → 详情缓存过期（10min TTL / pod 重启），重跑 `detail.py` 取概要后再答
- `ok:true` `data=null` → 该模块未生成，回文本"该客户的<模块>模块尚未生成"
- `ok:false` `error=unknown_topic` → 列可用主题，让销售重问
- `ok:true` → 读 `data` 切片作答

#### 步骤 Q4：作答（纯文本/Markdown）

- **输出形态**：纯文本/Markdown，可分点/加粗/列表。**不过 `validate_card.py`**，不输出卡片 JSON
- 数字 / 等级 / 概率**逐字引用脚本输出**（如"成交概率 85%"、"deal_level A"、"closeLevel A"）
- 多维问题分点作答（如"客户整体怎么样"→ 按 overall_tag + customer_type + closing_probability + emotion 分点）
- 引用 `profile_summary` / `reasoning_detail.summary` 等长文本时原样给出，不改写

### 防幻觉（Q&A 无校验器，最高优先级）

Q&A 轮不过 `validate_card.py`，防幻觉靠 grounding：

1. **只用脚本输出**：答案只能来自 `detail.py` 概要或 `query_detail.py` 切片。严禁用先验知识补全客户/画像信息
2. **概要不够就深挖**：不要凭概要里的摘要猜深挖细节——涉及 reasoning/evidence/雷达图/全部 basic_notes 就调 `query_detail.py` 取真实切片
3. **没有就说没有**：字段 null / 模块未生成（`hints.has_*=false` 或 `data=null`）→ 明说"该客户的 X 模块尚未生成"，**绝不编造**
4. **数字逐字**：概率/等级等数字必须与脚本输出一致，不四舍五入不臆测

### 允许使用的字段

- 概要：`brief.*`（复用 extract_fields + customer_overview + emotion_state + inferred_tags + usage_scenarios）+ `hints` + `topics` + `fetched_at` + `updated_at`
- 切片：`query_detail.py` 返回的 `data`（主题对应字段）
- 元信息：`phone`(脱敏) / `customer_name` / `update_url`

**严禁编造**完整手机号、report_url、分析字段值。Q&A 轮校验器兜不到，全靠你 grounding。

## 回调处理

收到 `【企微卡片按钮点击】task_id=gw_xxx key=select_49` 时：

- 用正则 `task_id=(\S+)\s+key=(\S+)` 提取
- task_id 是 Gateway 覆盖后的值（`gw_<uuid12>`），**不可用于业务**
- key 是 button 的 EventKey，**业务标识靠 key 编码 + 会话状态反查**

| key | 动作 |
|-----|------|
| `select_{customer_id}` | 从会话状态取该 id 对应 phone/name，进步骤 4（跑 profile.py） |
| `page_next` / `page_prev` | 从缓存 `search_result.items` 取对应页数据，手写新选择卡草稿，**过 `validate_card.py`** 输出（stdin 喂缓存的 search.py 输出） |
| `cancel` | 清空状态，回空闲 |

> 翻页/回调重发卡**必须经 `validate_card.py`**——把缓存的 search.py 输出作为 stdin，手写新页选择卡草稿校验后透传。不得凭记忆直接吐 JSON。

状态不匹配或超时（10分钟）→ 返回卡片："会话已超时，请重新发起查询"（过校验器）。

**卡片更新协议（可选）**：回复 `{"msgtype":"template_card","template_card":{...},"_update_task_id":"<原gw_task_id>"}` 时，Gateway 用点击事件 `response_code` 调 `update_template_card` 更新原卡。`response_code` 单次有效，72h 内可更新一次。`_update_task_id` 用回调里收到的 `gw_xxx`。

## 允许使用的字段（只能从脚本输出取，严禁编造）

- 检索：`items[].id / phone / name / deal_level / profile_sync_status / overall_tag`
- 画像：`fields.*` + `update_url` + `phone` + `customer_name`

`select_{id}` 必须对应真实 `items[].id`；画像卡 `jump_list` / `card_action` 的 url 必须是 `update_url`。**校验器核对，编造即整张弃稿回退到真实数据建的兜底卡。**

## Gotchas

1. **`profile_sync_status=0` 时仍需查完整画像**：列表返回此状态只表示"可能未生成"，但画像可能已存在。`profile.py` 总是调 `GET /profile/{phone}`，只有 `has_profile:false` 时才提示"画像生成中/无记录"。不要因 `profile_sync_status=0` 跳过查询。

2. **枚举映射 / JSON 字符串解析已在 `profile.py` 内**：`basic_notes` 的 `driver_license_status`（值 `{"key":"yes",...}`）、`purchase_type=replacement`→"换购"、`||` 多值拆分——全由 `profile.py` 处理，你拿到的 `fields` 已是中文干净值，不要再自己解析。

3. **`reasoning_detail` 不入卡**：每个推理对象含 steps/evidence/confidence，全展示会超 4000 字符。卡片只取 summary（已在 fields 聚合），完整 reasoning 仅在销售追问时展示。

4. **客户手机号在 `items[].phone`，非 `name`**：销售只看到 `name`（如"客户5678"）时容易取错字段。查画像必须用 `phone` 字段的完整手机号。

5. **翻页用客户端分页，不重复调 API**：`search.py --size 100` 一次性拉全缓存到 `search_result`，翻页从缓存取对应页。

6. **单条命中直接展示画像，无需确认**：`total=1` 直接跑 `profile.py` 展示画像卡。只有 `total>1` 才走选择卡。

7. **`sales_rep` 不匹配不直接报"权限不足"**：用"该客户可能不归属您"话术，避免引起销售抵触。

8. **「查看完整画像」用 `jump_list` + `card_action`，不用 `button_list`**：text_notice 卡片无按钮交互，通过 `jump_list` 的 `type:1 url` 行和 `card_action` 实现点击跳转，不触发回调。url 必须 == stdin 的 `update_url`。

9. **text_notice 画像卡无 `emphasis_content` / `button_list`**：展示关键数据用 `sub_title_text`（`\n` 换行分隔动机/偏好/抗性）或 `horizontal_content_list`。换客户靠重新输入检索，不设「换一个」按钮。

10. **task_id 填占位值即可**：Gateway 用 `gw_<uuid12>` 覆盖，不能依赖回调 task_id 携带业务信息。校验器会自动补占位 task_id。

11. **所有出站卡过 `validate_card.py`**：选择卡、画像卡、翻页卡、错误卡——只要输出 JSON，都先交校验器。校验失败会自动回退到真实数据建的兜底卡，仍输出合法 JSON。

12. **Q&A 别把全量画像喂会话**：`detail.py` 已把 90KB 存盘只返概要。**不要**用 `execute_code` 自己调 `/profile/{phone}` 把全量读进上下文——会污染会话历史。深挖走 `query_detail.py` 取小切片。

13. **画像可变，概要不可无限复用**：与试驾报告（不可变）不同，画像可能被销售更新。概要带 `fetched_at`，**10min 内可复用**，超时或销售说"刷新"必须重跑 `detail.py`。query_detail 缓存 mtime 超 10min 会返 `cache_missing` 触发重取。

14. **`query_detail.py` 缓存会过期**：详情存 `${HERMES_HOME:-$HOME}/.skill_tmp/cp_detail_{phone}.json`（0600），10min TTL 或 pod 重启后过期。收到 `cache_missing` 别慌——重跑 `detail.py` 取概要（会重新存盘），再答。

15. **模块 null 不是"成交概率 0%"**：`hints.has_customer_overview=false` 或切片 `data=null` 表示**该模块未生成分析**，不是客户成交概率为 0。回"该客户的客户总览模块尚未生成"，别编造"成交概率 0%"。

16. **Q&A 输出纯文本不是卡片**：追问轮**不过 `validate_card.py`**，不输出 `{"msgtype":...}` JSON。直接 Markdown 文本作答。卡片轮和问答轮输出形态不同，别混。

17. **Q&A 用 `current_customer.phone`，无多命中歧义**：画像卡只展示一个客户，`showed_profile` 后 `current_customer.phone` 已设。不像试驾报告可能多命中——直接用，不必从列表反查。

## 错误处理

| 场景 | 话术 | 输出形式 |
|------|------|---------|
| 客户不存在（total=0） | "未找到匹配的客户。请确认手机号或姓名，或确认该客户是否归属您。" | 纯文本（不建卡） |
| 权限不足（HTTP 403） | "该客户可能不归属您，无法查询。" | 纯文本 |
| 客户存在但无画像（has_profile=false） | "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。" | 纯文本 |
| API Key 无效（HTTP 401） | "系统暂时无法访问客户数据，请稍后重试或联系管理员。" | 纯文本 |
| 服务器错误（5xx/超时） | "系统繁忙，请稍后重试。" | 纯文本 |

> 错误场景直接回复话术文本，**不需过校验器**（校验器在 0 命中/失败时也会输出同样话术文本兜底）。

## References（仅按需查阅）

以下文件仅在遇到本 SKILL.md 未覆盖的 edge case 时查阅，**不需要预读**：

| 文件 | 何时查阅 |
|------|---------|
| `references/api-spec.md` | 排查非标准错误码或看 search.py/profile.py 结构化输出 schema |
| `references/card-protocol.md` | 需要卡片更新协议（`_update_task_id`）或排查卡片渲染问题 |
| `references/profile-model.md` | 需要查看完整画像字段列表或 `basic_notes` 全部属性键 |
| `references/_wecom_card_doc.md` | 企微 template_card 官方字段规范（完整） |
| `references/api-spec.md` §5 | 查 detail.py / query_detail.py 输出 schema、深挖主题清单 |
| `scripts/detail.py` / `scripts/query_detail.py` | Q&A 取数 + 深挖切片脚本 |
