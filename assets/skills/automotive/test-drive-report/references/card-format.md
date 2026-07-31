# 卡片格式与渲染规范

## 输出契约

Agent 每轮回复二选一，**不能混用**：

1. **卡片消息**：回复**整体就是** `validate_card.py` 的 stdout——单个 JSON 对象，从 `{` 开头、到 `}` 结尾，**不加前后说明文字，不加 ```json 代码围栏**。gateway 的 `extract_card_json` 会从回复里括号配平提取卡片 JSON 下发；一旦混入说明文字或代码围栏，卡片可能失效或被当 markdown。
   - ✅ 回复整体即：`{"msgtype":"template_card","template_card":{...}}`
   - ❌ 前后带"查询成功，找到1条..."+ ```json``` 围栏包 JSON +"点击查看..."
2. **纯文本消息**（追问、0 命中话术、状态说明）：直接输出文本，走 markdown 通道。

> 卡片轮次只输出 JSON；要说明（如"好的，我查一下"）放到上一轮纯文本里，卡片轮次本身保持纯 JSON。
>
> 卡片 JSON 由 AI 手写、交 `validate_card.py` 校验消毒后输出。**校验器会强制字段约束（项数/字数/必填）并拦截幻觉 url**，AI 不必手算字数，但必须只用 `run.py` 返回的真实 items 字段。

## 设计原则

- **形态不固定，AI 按意图选**：根据用户问题 + `items`/`hints`，从下方「text_notice 布局变体」里选最优形态，手写 JSON。
- **点卡即跳转，无二次确认**：命中 1 条→整卡可点 + "查看完整报告"按钮；命中多条→每行可点跳转对应报告；无命中→纯文本。选错就让销售重新提问。
- **美化靠填满可选字段 + emoji 视觉标记**：企微 template_card 字段有限，不能自定义样式/布局，但可用 `source.icon_url`、emoji 前缀（👤/🚙/🕐/📱/🚗）让卡片信息密度和视觉层次更丰富。

## text_notice 布局变体（AI 选型参考）

以下变体都是 `card_type: "text_notice"`，区别在字段组合与强调。AI 根据用户意图选其一，手写 JSON。

### 变体 A：摘要卡（1 条命中 / 问某客户某属性）

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "source": {"desc": "试驾报告系统"},
    "main_title": {"title": "🚗 王先生的试驾报告"},
    "horizontal_content_list": [
      {"keyname": "👤 客户", "value": "王先生 · 176****5538"},
      {"keyname": "🚙 车型", "value": "M817-城市科技版A"},
      {"keyname": "🕐 时间", "value": "2026-05-27 13:59 - 16:05"}
    ],
    "jump_list": [{"type": 1, "url": "<report_url>", "title": "查看完整报告"}],
    "card_action": {"type": 1, "url": "<report_url>"}
  }
}
```

> 问车型时把"🚙 车型"行置顶；问时间时把"🕐 时间"行置顶——按用户意图排字段顺序。

### 变体 B：列表卡（2-6 条命中）

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "source": {"desc": "试驾报告系统"},
    "main_title": {"title": "🚗 找到 3 条试驾报告"},
    "sub_title_text": "👇 点击对应客户查看完整试驾报告",
    "horizontal_content_list": [
      {"keyname": "1 王先生", "value": "📱 5538 🚙 M817", "type": 1, "url": "<report_url_1>"},
      {"keyname": "2 李女士", "value": "📱 8476 🚙 M9", "type": 1, "url": "<report_url_2>"}
    ],
    "card_action": {"type": 1, "url": "<report_url_1>"}
  }
}
```

> `hints.cross_time_slot` 为 true 时，每行 `value` 追加 ` 🕐 上午`/` 🕐 下午` 区分时段。

### 变体 C：计数强调卡（问"有几个"/"有多少"）

```json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "source": {"desc": "试驾报告系统"},
    "main_title": {"title": "🚗 今天上午试驾"},
    "emphasis_content": {"title": "3", "desc": "位客户"},
    "sub_title_text": "👇 点击查看完整报告",
    "horizontal_content_list": [
      {"keyname": "1 王先生", "value": "📱 5538 🚙 M817", "type": 1, "url": "<report_url_1>"}
    ],
    "card_action": {"type": 1, "url": "<report_url_1>"}
  }
}
```

> `emphasis_content.title` 放大数字 N，`desc` 放"位客户"/"条报告"。下方仍带可点列表。

### 变体 D：纯文本（0 命中）

```
没找到匹配的试驾报告——可能是试驾还没完成（报告生成要几分钟），或者这位客户不归您名下。要不要换个日期或手机号尾号再查？
```

> 不建卡，直接走 markdown 通道。



## 字段映射与格式化规则

各变体共用的字段取值规则（AI 手写卡片时按此格式化，校验器只查约束不查格式）：

| 卡片字段 | 来源 | 格式化 |
|---------|------|--------|
| `source.icon_url` | 托管图片 URL | 可选；无则只保留 `source.desc`="试驾报告系统"。需 HTTPS 公网可访问 |
| `main_title.title` | 摘要卡固定"🚗 试驾报告"；列表卡"🚗 找到 N 条试驾报告" | — |
| `sub_title_text` | 列表卡固定"👇 点击对应客户查看完整试驾报告" | — |
| `emphasis_content.title` / `.desc` | 计数强调卡用 | 大数字 N + "位客户"/"条报告" |
| 客户信息 value | `customer_name` + `customer_phone` | "客户名 · 脱敏手机"；`customer_name` 为空用"客户+尾号"（如"客户6683"） |
| 试驾车型 value | `vehicle` | 直接用；空则 `vehicle_model` + "-" + `vehicle_variant` |
| 试驾时间 value | `start_time`/`end_time` | "YYYY-MM-DD HH:MM - HH:MM" |
| 列表行 keyname | 序号 + `customer_name` | "1 王先生"（≤5 字）；`customer_name` 为空用"客户+尾号" |
| 列表行 value | `customer_phone` 尾号 + `vehicle_model` | "📱 5538 🚙 M817"（≤26 字）；跨时段追加" 🕐 下午" |
| `jump_list[].url` / `card_action.url` / 行 `url` | **必须** `report_url`（来自 items） | 校验器核对，编造 url 整张弃稿 |
| `card_action` | text_notice **必填** | `type:1` + `url`；缺失校验器自动注入 `items[0].report_url` |

> `customer_phone` 在 items 里已脱敏（`176****5538`），展示可直接用或取尾号 4 位。
> 多报告场景 `card_action.url` 用第一个报告的 URL——销售应点具体行选报告，点卡片其他区域会打开第一个报告。

## 字段校验清单（`validate_card.py` 强制）

`text_notice` 字段约束，校验器自动 enforce（AI 不必手算，超限自动截断）：



| 字段 | 必填 | 约束 |
|------|------|------|
| `card_type` | ✅ | 固定 "text_notice" |
| `card_action` | ✅ | **必填**；`type` ∈ [1,2]，`type:1` 时 `url` 必填 |
| `main_title` 或 `sub_title_text` | 至少一项 | — |
| `horizontal_content_list` | ❌ | 有则 ≤**6** 项；每项 `keyname`(建议≤5字) + `value`(≤26字)；行可点击需加 `type:1` + `url` |
| `jump_list` | ❌ | 有则 ≤**3** 项；每项 `type:1` + `title`(建议≤13字) + `url`。单条命中用它放"查看完整报告"CTA |
| `source` | ❌ | 可选；`icon_url` 需 HTTPS 公网可访问图片 |
| `sub_title_text` | ❌ | 可选；建议≤112字 |
| `task_id` | ❌ | 仅 `action_menu` 存在时必填 |

> ⚠️ **不要用 `news_notice`**：它的 `image_text_list` 字段不存在（news_notice 实际用 `card_image` + `image_text_area` + `vertical_content_list` + `jump_list`，是单条图文通知），用了会"消息渲染失败"。

## 跳转机制（无需 adapter 入站支持）

| 卡片类型 | 跳转字段 | 行为 |
|---------|---------|------|
| `text_notice`（单条命中） | `jump_list[0].url` + `card_action.url` | 点"查看完整报告"按钮 或 点整张卡 → 企微客户端打开 report_url |
| `text_notice`（多条命中） | `horizontal_content_list[].url` | 点对应行 → 企微客户端打开该报告 url |

都是 URL 跳转，由企微客户端直接处理，**不触发 `template_card_event` 回调**，adapter 无需解析入站事件。这也是本 Skill 不用 `button_interaction`（按钮 `button_list` 只有 `key` 回调、无 `url`）的原因——跳转用 URL 更直接，且省掉 adapter 入站扩展。
