# 企微 Template Card 协议

本 Skill 通过 Gateway 渲染企微原生 `template_card`。本文档只记录**本 Skill 特有的约定和 Gateway 行为**，企微官方字段规范见 `references/_wecom_card_doc.md`。

## Gateway 实现行为

skill 侧必须知晓以下 Gateway 行为，否则设计会脱靶：

1. **卡片透传**：Gateway `send_card_message` 直接把 `{msgtype, body}` 透传给企微，body 字段原样下发。多带非官方字段（如 `data`/`state_token`）可能触发企微 errcode。

2. **task_id 被 Gateway 覆盖**：Gateway 发送时若检测到 `template_card.task_id`，会用 `gw_<uuid12>` 覆盖。skill 仍需填 task_id（button_interaction 官方必填），但**不能依赖回调 task_id 携带业务信息**。

3. **按钮点击回传格式**：Gateway 解析企微 `MsgType=event` + `Event=template_card_event`，从 XML 提取 `TaskId`/`EventKey`/`ResponseCode`，合成文本送达 Skill：
   ```
   【企微卡片按钮点击】task_id=gw_xxx key=select_49
   ```
   **只回传 task_id + key，无 data 字段**。skill 需从 key 解析业务标识 + 会话状态反查业务数据。

4. **卡片更新协议**：skill 回复 `{"msgtype":"template_card","template_card":{...},"_update_task_id":"<tid>"}` 时，Gateway 用点击事件的 `response_code` 调 `update_template_card` 更新原卡。`response_code` 单次有效，72h 内可更新一次。

5. **纯 JSON 识别**：skill 回复必须以 `{` 开头纯 JSON（支持 ```json``` 代码围栏容错），Gateway 据此判断卡片/文本。

## 输出纯度要求

**卡片消息的最终响应体必须是纯 JSON，从 `{` 开始到 `}` 结束，前后不能有任何其他字符。**

- ❌ `好的，请选择：\n{...}\n点击即可。` → Gateway 降级为纯文本，卡片不渲染
- ✅ `{...}` → Gateway 识别为卡片并渲染
- 所有话术（问候/确认/提示/总结）必须写进卡片字段（`main_title.title` / `sub_title_text` / `horizontal_content_list[].value`）
- 需要分段说明时，整合到一张卡片，而非 JSON + 文本混合

> 卡片 JSON 现由 AI 手写、经 `scripts/validate_card.py` 校验消毒后输出。`button_list` ≤6 / key 唯一 / `select_{id}` 真实 / 画像卡「查看完整画像」url 真实，由校验器强制；AI 不必手算字数，但必须只用真实数据。校验失败自动回退到 `build_card.py` 用真实数据建的兜底卡。

## 本 Skill 使用的卡片类型

| card_type | 使用场景 |
|-----------|---------|
| `button_interaction` | 客户选择列表（列表翻页） |
| `text_notice` | 画像卡（完整画像展示） |

> 画像卡用 `text_notice`，选择卡用 `button_interaction`。`text_notice`/`news_notice` 的官方字段规范见 `_wecom_card_doc.md`。

## button_interaction 约定

### 客户选择卡（列表翻页）

`button_list` 官方上限 6。采用**客户端分页**：`size=100` 一次性拉取全部结果缓存，每页展示 5 个客户 + 1 个翻页按钮。

| 场景 | 按钮布局 | 按钮数 |
|------|---------|--------|
| 第 1 页（有更多） | 5 客户 + 「下一页」`page_next` | 6 |
| 中间页 | 4 客户 + 「上一页」`page_prev` + 「下一页」`page_next` | 6 |
| 最后一页 | 剩余客户 + 「上一页」`page_prev` | ≤6 |
| 总数 ≤5 | 全部客户，无翻页按钮 | ≤5 |

按钮文案只展示「用户名 + 手机号」（`张先生 · 138****8000`），不放标签/级别等额外信息。**不设「重新搜索」按钮**——销售想换其他客户直接输入文本重新检索。

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "button_interaction",
    "source": {"desc": "客户画像"},
    "main_title": {"title": "找到 8 位匹配客户（第 1/2 页）"},
    "sub_title_text": "点击客户名称选择，或输入手机号片段精确查找",
    "task_id": "task_select_placeholder",
    "button_list": [
      {"text": "张先生 · 138****8000", "style": 1, "key": "select_49"},
      {"text": "李女士 · 139****1234", "style": 1, "key": "select_52"},
      {"text": "王先生 · 137****6666", "style": 1, "key": "select_61"},
      {"text": "赵女士 · 136****3333", "style": 1, "key": "select_68"},
      {"text": "陈先生 · 135****1111", "style": 1, "key": "select_73"},
      {"text": "下一页", "style": 2, "key": "page_next"}
    ]
  }
}
```

### 画像卡（完整画像展示）

画像卡用 `text_notice`：`horizontal_content_list`（≤5项）塞关键画像字段，动机/偏好/抗性放 `sub_title_text`（`\n` 换行）。跳转用 `jump_list`「查看完整画像」+ `card_action`（必填）。无 `button_list`、无 `task_id`。

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "source": {"desc": "客户画像"},
    "main_title": {"title": "客户5678 · 139****5678", "desc": "A级意向 · 务实家用型"},
    "sub_title_text": "动机：家庭代步+安全\n偏好：空间/油耗\n抗性：价格/品牌力",
    "horizontal_content_list": [
      {"keyname": "整体标签", "value": "务实家用型"},
      {"keyname": "意向车型", "value": "追光"}
    ],
    "jump_list": [{"type": 1, "url": "<update_url>", "title": "查看完整画像"}],
    "card_action": {"type": 1, "url": "<update_url>"}
  }
}
```

**字段取舍优先级（取前 5 项填 `horizontal_content_list`）：**
1. 整体标签（决策风格）
2. 意向车型 / 预算区间 / 购买阶段（核心交易要素）
3. 突破策略（销售行动指引）
4. 人格画像 → 放 `main_title.desc`（截断20字）
5. 动机 / 偏好 / 抗性（次重要，放 `sub_title_text`，`\n` 换行）
6. reasoning steps/evidence、usage_scenarios 详细项（不放卡片，销售追问时再展示）

> 枚举值（如 `purchase_type=replacement`）必须映射中文（"换购"），映射表从 `GET /api/v1/remote/config/note-attributes` 获取。`basic_notes` 中 JSON 字符串字段需解析取 `key`。详见 `references/profile-model.md`。

> `text_notice` 不支持 `emphasis_content` / `button_list`（无按钮交互）。换客户靠重新输入检索，不设「换一个」按钮。

## 回调协议

销售点击按钮后，Gateway 合成文本送达 Skill：

```
【企微卡片按钮点击】task_id=gw_xxx key=select_49
```

**解析规则：**
- 用正则 `task_id=(\S+)\s+key=(\S+)` 提取 task_id 和 key
- task_id 是 Gateway 覆盖后的值（`gw_<uuid12>`），**不可用于业务**，仅用于卡片更新协议
- key 是 button 的 EventKey，**业务标识靠 key 编码 + 会话状态反查**

**业务数据反查机制：**
- skill 发客户列表卡片时，把 `key → {customer_id, phone, name, level}` 缓存进会话状态
- 收到 `key=select_49` 后，按 `select_` 前缀识别为选择动作，取 `49` 作为 customer_id，从会话状态取 phone/name/level
- 会话状态由 skill 内部维护（不依赖 Gateway 回传 state_token）

**状态校验：**
- 收到回调后校验 `last_action` 是否在预期状态（如 `searched_list` 才接受 `select_{id}`）
- 状态不匹配或超时（10分钟无操作）→ 返回卡片："会话已超时，请重新发起查询"
- 状态匹配 → 按 `key` 执行对应动作，更新 `last_action`

**卡片更新（可选）：**
- skill 回复 `{"msgtype":"template_card","template_card":{...},"_update_task_id":"<原task_id>"}` 时，Gateway 用点击事件的 `response_code` 调 `update_template_card` 更新原卡
- 限制：一个 `response_code` 只能更新一次，72h 内有效；`task_id` 必须用回调里收到的 `gw_xxx`

**动作类型（key）汇总：**

| key | 触发场景 | Skill 动作 |
|-----|---------|-----------|
| `select_{customer_id}` | 客户列表选择 | 从会话状态取 phone，进入查询路径 |
| `restart` | 重新搜索（错误卡「重新搜索」按钮） | 回到搜索 |
| `page_next` | 下一页（客户列表翻页） | 从缓存的 search_result 取下一页数据，发新卡片 |
| `page_prev` | 上一页（客户列表翻页） | 从缓存的 search_result 取上一页数据，发新卡片 |
| `cancel` | 取消 | 清空状态，回空闲 |

## 文本兜底

当卡片按钮失效或销售习惯文本输入时，仍支持文本兜底：
- 客户选择：输入手机号片段重新检索
- 任意状态：输入"取消"清空状态

Skill 检测到纯文本输入时，按当前状态解析意图，不依赖按钮回调。

## 常见错误

| 错误 | 原因 | 修正 |
|------|------|------|
| 卡片不渲染，变成纯文本 | 响应体非纯 JSON（前后有话术）或缺失外层 `msgtype`/`template_card` | 严格输出 `{...}` 纯 JSON |
| 卡片报错"card_action 必填" | `text_notice`/`news_notice` 缺少 `card_action` | 补充 `card_action`，无跳转时填 `{"type":1,"url":"https://work.weixin.qq.com"}` 占位（**type=0 企微不接受 errcode=42045**） |
| 卡片报错"task_id 必填" | `button_interaction` 未填 task_id | 补充 task_id（Gateway 会覆盖，填占位值即可） |
| 卡片报错"main_title 必填" | `button_interaction` 未填 main_title | 补充 `main_title` |
| button_interaction 关键数据不显示 | 用了 `emphasis_content`（button_interaction 不支持） | 改用 `sub_title_text` 或 `horizontal_content_list` |
| 按钮点击无回调 | `key` 重复或超1024字节 | 确保每个 button 的 key 唯一且≤1024字节 |
| 回调拿不到业务数据 | 依赖 data 扩展字段（Gateway 不回传） | key 编码 customer_id + 会话状态反查 phone |
| `horizontal_content_list` 只显示前6项 | 超过6项被截断 | 按字段优先级取前6，次重要字段放 `sub_title_text` |
