# WeCom 渠道 — 卡片消息与交互按钮设计

> 适用 develop 分支 gateway（`services/gateway/app/channel/wecom.py` + `card_utils.py` + `dispatcher.py`）。
> WeCom 渠道路由：`POST /api/gateway/channel/wecom/{agent_id}/callback`。

## 一、只读卡片透传

Profile 回复为带 `msgtype` 的 JSON 字符串时，gateway 按卡片透传（`WeComAdapter.send_reply` → `send_card_message`）。

### 1.1 支持的卡片类型

| msgtype | 用途 |
|---------|------|
| `template_card` | 模板卡片（text_notice / button_interaction / vote_interaction 等） |
| `textcard` | 文本卡片（含链接跳转） |
| `news` | 图文消息 |
| `mpnews` | 图文消息（企微专用） |
| `markdown` | Markdown 富文本 |
| `image` | 图片消息 |

### 1.2 回复格式约定（方案 A）

Profile 需要发卡片时，回复 `content` 输出成企微 `message/send` 形态的 JSON：

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "main_title": {"title": "试驾报告", "desc": "刘小明 · 6月10日"},
    "sub_title_text": "意向：高",
    "card_action": {"type": 1, "url": "https://..."}
  }
}
```

gateway 只识别 `msgtype`、补 `touser` / `agentid` 后透传，不做字段映射。

### 1.3 卡片 JSON 容错提取（`card_utils.extract_card_json`）

模型回复往往不是纯 JSON：卡片 JSON 前后可能夹带说明文字、` ```json ` 代码围栏，一条回复还可能含**多个**卡片 JSON。`card_utils.extract_card_json(content, required_key="msgtype")` 用字符串感知的括号配平逐个捞出含 `msgtype` 的 JSON 对象，返回 `(obj, before, after)`：

- `obj`：命中的卡片 dict（未命中为 `None`）
- `before`：该 JSON 之前的文本（前导说明 / 围栏）
- `after`：之后的文本（可能含更多卡片，调用方循环提取）

`WeComAdapter.send_reply` 循环调用：按原文顺序逐段发送「前导文本 → 卡片 → 后续文本/卡片」，使「文本 + 多卡片」共存时每个卡片都正确渲染（PR #83）。扫描识别 `"..."` 字符串与转义、不计字符串内括号，故能跳过 prose 里的 `{示例}`、`{a:1}` 等非法片段；反引号围栏天然忽略。误判风险极低（需 prose 恰好含合法 JSON 对象且带 `msgtype`）。

此外，配平失败时若片段含 `required_key`，会尝试逐个补全尾部 `}`（最多 5 个）修复残缺 JSON。

按钮点击就地更新路径（`dispatcher._process_card_click`，企微实际路径）同样用 `extract_card_json(required_key="_update_task_id")` 提取更新指令。

### 1.4 task_id 唯一性覆盖

`template_card` 的 `task_id` 须全局唯一（企微 30 天内不允许重复，否则 errcode 42014）。agent 生成的可能重复（如照抄示例数字）→ gateway 发卡片时用 `"gw_" + uuid.uuid4().hex[:12]` 覆盖。

点击回传此 id，agent 在更新回复里回显 `_update_task_id` 即可，无需 agent 保证唯一。

此外，对 `card_type=="button_interaction"` 的 `button_list`，无 `key` 的按钮自动补 `"gw_btn_" + uuid` 前 8 位，并删除 `url` 字段（防企微 errcode 42039）。

### 1.5 降级策略

| 场景 | 行为 |
|------|------|
| 卡片发送成功（errcode=0） | 正常展示卡片 |
| 卡片发送失败（errcode≠0） | 降级文本"⚠️ 消息展示失败，请重试" |
| 有 msgtype 但不支持/残缺 | 回复"消息格式异常，请重试"（不发原始 JSON） |
| 非 JSON / 无 msgtype | 当纯文本发 |

---

## 二、交互卡片按钮点击（button_interaction）

### 2.1 点击事件回调

用户点 `button_interaction` 卡片按钮后，企微回调 `template_card_event` 事件（`MsgType=event`）。

**XML 关键字段：**

| 字段 | 说明 |
|------|------|
| `FromUserName` | 企微 user_id |
| `TaskId` | 原卡片发送时的 task_id（已被 gateway 覆盖为 `gw_xxx`） |
| `EventKey` | 所点按钮的 key |
| `CardType` | `button_interaction` |
| `ResponseCode` | 单次有效的更新凭证（更新卡片必带） |

### 2.2 处理流程

```
企微回调 event → router → adapter.parse_incoming
  → 识别 template_card_event + button_interaction
  → 设置 raw_message["card_click"]=True
  → 平铺 task_id / event_key / response_code
  → 返回 MessageEvent（text = 合成消息）
  ▼
dispatcher._process_one
  → Step 0.6 检测 raw_message["card_click"]
  → 走 _process_card_click 专用流程
  ▼
_process_card_click（自行负责 ensure_engine_ready + session + profile + forward）:
  1. 合成消息送达 Profile: 【企微卡片按钮点击】task_id=<tid> key=<key>
  2. _forward_message_with_retry → Profile 回复
  3. 回复处理：
     _process_card_click 调 adapter.send_card_click_reply
       → adapter 内部 _parse_card_update 探测
       → 含 _update_task_id 则 update_template_card 就地更新原卡片
       → 其他 card/text → 发新消息
       → 空 → 仅日志
```

> 注：`_process_card_event`（检测 `raw_message["card_event"]` 键）在企微渠道不可达，保留为其他渠道预留的路径。企微实际走 `card_click` 键 + `_process_card_click`。

### 2.3 Profile 回复约定

#### 更新原卡片（就地更新）

Profile 回复带 `_update_task_id` 的 JSON：

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "button_interaction",
    "main_title": {"title": "已处理"},
    "button_list": [{"text": "已处理", "style": 1, "key": "call"}]
  },
  "_update_task_id": "gw_xxxxxxxxxxxx"
}
```

gateway 调 `update_template_card`，用 `_update_task_id` 定位原卡片，带 `response_code` 更新。

#### 发新消息

Profile 回复不带 `_update_task_id`（普通 card 或 text）→ 走 `send_reply` 发新消息。

### 2.4 update_template_card 必填字段

| 字段 | 来源 | 说明 |
|------|------|------|
| `task_id` | 点击事件 `TaskId`（由 `_update_task_id` 指定） | 定位原卡片 |
| `response_code` | 点击事件 `ResponseCode` | **单次有效**，同一按钮点第二次 errcode 60140 |
| `template_card.main_title.title` | Profile 回复 | 非空，否则 errcode 41016 |
| `template_card.button_list` | Profile 回复 | 更新的按钮，`key` 须与原卡片一致 |

---

## 三、企微 API 超时配置

| 接口 | 超时 | 说明 |
|------|------|------|
| `gettoken` | 30s | 部分 environment 到 qyapi.weixin.qq.com 较慢（基线 ~4.5s，偶发 spike >10s） |
| `message/send` | 30s | 同上 |
| `update_template_card` | 30s | 同上 |

> 原 10s 超时导致卡片下发/按钮更新偶发"渲染失败"。调到 30s 吸收 spike。

---

## 四、可靠性机制

| 机制 | 说明 |
|------|------|
| **MsgId 幂等去重** | 企微重试窗口（120s）内同 `MsgId` 重复投递只处理一次（dispatcher `_dedup`） |
| **per-agent 串行** | 同一 agent 的消息经 per-agent 队列串行处理，避免并发重复部署 / 乱序回复 |
| **引擎转发重试** | 连接级错误（503/超时）指数退避重试 3 次（1s/2s/4s） |
| **冷启动 UX** | 引擎刚启动时发"🤖 正在启动…"占位，就绪后发回复 |
| **卡片 task_id 覆盖** | `template_card` 发送时用 `"gw_" + uuid` 覆盖，防企微 42014 |
| **卡片发送降级** | errcode≠0 → 降级文本"⚠️ 消息展示失败，请重试"；格式异常 → "消息格式异常" |
