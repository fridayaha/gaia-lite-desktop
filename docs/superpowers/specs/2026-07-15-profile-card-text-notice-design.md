# 画像卡改 text_notice + 时延优化设计

**日期**：2026-07-15
**Skill**：customer-profile-update
**版本**：2.2.0 → 2.3.0

## 背景

`customer-profile-update` 的画像卡当前用 `button_interaction` 模板：`horizontal_content_list` 展示画像字段 + 末项「更新画像」url 跳转 + `button_list` 一个「换一个」按钮。实测画像查询端到端 27-66s，其中卡片生成那一次 LLM 调用 10-22s（输出 800-2200 token），是单点最长。

时延分析（详见 trace）：
- LLM 循环占 80-90%+，卡片生成是单次最长调用。
- 卡片结构越复杂（button_list + url 行 + 多约束），AI 生成 token 越多 → 越慢。
- WeCom 回调重试经核查**已被 gateway 去重**（`ChannelDispatcher` 单例 + MsgId 去重 120s），非时延放大器；多次回调是企微客户端用新 MsgId 重发（网络），gateway 无法/不应去重。

## 需求

1. **减少整体时延**：优化方向有利于降低端到端时延。
2. **画像卡改 text_notice**：所有画像卡（total=1 直跳 + total>1 选完后）改用 `text_notice` 模板；去掉「换一个」按钮 + 去掉「更新画像」；底部改「查看完整画像」跳转（参考试驾报告卡片）。选择卡（total>1 客户列表）不变，仍 `button_interaction`。
3. **画像卡美化**：`sub_title_text` 中动机/偏好/抗性换行展示。

## 澄清决策

- **「查看完整画像」URL**：复用现有 `update_url`（profile.py 输出的画像系统 profile 页，原「更新画像」用的就是它），仅文案改「查看完整画像」。
- **适用范围**：所有画像卡都改 text_notice（total=1 + total>1 选完后），选择卡不变。
- **sub_title 换行**：用 `\n` 分隔（"动机：…\n偏好：…\n抗性：…"），发测试卡验证 WeCom 是否渲染换行；不渲染则回退" · "拼接。

## 设计

### 1. 画像卡结构（text_notice）

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
      {"keyname": "意向车型", "value": "追光"},
      {"keyname": "预算区间", "value": "30-40万"},
      {"keyname": "购买阶段", "value": "需求确认"},
      {"keyname": "突破策略", "value": "金融方案"}
    ],
    "jump_list": [{"type": 1, "url": "<update_url>", "title": "查看完整画像"}],
    "card_action": {"type": 1, "url": "<update_url>"}
  }
}
```

**vs 现状（button_interaction）**：
- `card_type`：button_interaction → text_notice
- 去 `button_list`（「换一个」）
- 去 `horizontal_content_list` 末项「更新画像」（type:1 url 行）
- 加 `jump_list`（「查看完整画像」→ update_url）
- 加 `card_action`（→ update_url，text_notice 必填项）
- `sub_title_text`：动机/偏好/抗性 `\n` 换行（替代" · "拼接）
- 去 `task_id`（text_notice 不需要，仅 button_interaction 用于按钮回调）

**字段优先级**（horizontal_content_list ≤5，不变）：
1. 整体标签 ← fields.overall_tag
2. 意向车型 ← fields.intended_model
3. 预算区间 ← fields.budget_range
4. 购买阶段 ← fields.current_stage
5. 突破策略 ← fields.breakthrough_point

**sub_title_text**（≤112 字）：`动机：{motivations}\n偏好：{preferences}\n抗性：{resistances}`（缺项跳过该行；全缺→"暂无动机/偏好/抗性摘要"）。

**main_title**：`{customer_name} · {mask_phone}`，desc = `{deal_level}级意向 · {personality[:20]}`（缺 deal_level 回落 personality）。

### 2. 改动文件

| 文件 | 改动 |
|---|---|
| `scripts/build_card.py` | `build_profile_card` 改 text_notice 结构（jump_list/card_action/\n sub_title，去 button_list/task_id/更新画像行） |
| `scripts/validate_card.py` | 画像卡放行 text_notice（选择卡仍 button_interaction）；`_sanitize_profile` 校验 jump_list/card_action url==update_url；去 button_list 校验；保留 card_action（text_notice 必填）；hcl 截断不变 |
| `SKILL.md` | 步骤5 画像卡指令改 text_notice（去「换一个」/「更新画像」/button_list 约束，加「查看完整画像」跳转 + \n sub_title + card_action 必填）；参考 tdr 卡片样式 |
| `references/card-protocol.md` | 画像卡段更新 text_notice（去"本 Skill 当前只用 button_interaction"，补 text_notice 画像卡字段表） |
| `references/api-spec.md` | 画像卡示例同步 text_notice |
| `scripts/tests/test_build_card.py` | `build_profile_card` 断言 text_notice（card_type/jump_list/card_action/\n sub_title/无 button_list） |
| `scripts/tests/test_validate_card.py` | 画像卡校验断言 text_notice（放行 + jump_list/card_action url 校验 + 兜底建 text_notice） |
| `manifest.json` | 版本 2.2.0 → 2.3.0 |

### 3. validate_card.py 校验逻辑调整

- `ALLOWED_CARD_TYPE` 拆分：选择卡 `button_interaction`，画像卡 `text_notice`。
- `_sanitize_profile`：
  - `card_type == "text_notice"`（否则兜底）
  - `jump_list`（如有）每个 type:1 url 必须 == update_url（幻觉 url → 兜底）
  - `card_action`（必填）url 必须 == update_url；缺失 → 注入 `{"type":1,"url": update_url}`（不兜底，保留 AI 草稿布局）；幻觉 url → 兜底
  - 去 `button_list` 校验（text_notice 无按钮；如有 button_list 删除）
  - 保留 `card_action`（不再 pop；text_notice 必填）
  - `horizontal_content_list` 截断 ≤6、keyname≤5、value≤26（不变；不再有"更新画像"url 行）
  - 缺 `main_title` → 注入默认；不要求 task_id
- `_sanitize_selection` 不变（button_interaction）。
- 兜底：`build_profile_card`（text_notice）。

### 4. 时延优化

- **text_notice 卡更简单**：无 button_list + 无「更新画像」url 行 → 卡片生成 LLM 输出 token 减少（当前 800-2200 tok → 预计 ~600-1000 tok）→ 最长那次调用（10-22s）省 ~2-5s。
- **SKILL.md 指令简化**：画像卡规则从 button_interaction（按钮约束 + url 校验 + emphasis_content 禁忌 + task_id）简化为 text_notice（jump_list + card_action + 字段）→ AI 决策 + 生成更快。
- `\n` sub_title 把动机/偏好/抗性收进一个字段（vs 散在 hcl 或多行）→ 结构更简单。

不在本次范围（时延相关但另行评估）：跳过 finalize LLM（hermes 框架需 LLM 收尾，不可控）、裁会话历史（DeepSeek 缓存已压住 prefill 延迟，主要省成本不省延迟）。

### 5. \n 换行回退

- `build_profile_card` 兜底 + SKILL.md 指令均用 `\n`。
- 实现后发一张测试卡到企微，确认 sub_title_text 的 `\n` 是否渲染换行。
- **若渲染**：保留 `\n`。
- **若不渲染**：`build_profile_card` 回退" · "拼接（单行），SKILL.md 同步；或改用 horizontal_content_list 行（动机/偏好/抗性 各一行，需砍 priority 字段 5→3 腾 hcl 槽位）。

### 6. 选择卡不变

total>1 的客户选择卡仍 `button_interaction`（select_{id} 按钮 + 翻页 page_next/page_prev），`_sanitize_selection` + `build_selection_card` 不动。

### 7. 错误卡不变

`build_error_card`（not_found/forbidden/no_profile/syncing）仍 button_interaction（重新搜索/结束按钮），不动。

## 测试

- `test_build_card.py`：`build_profile_card` 返回 text_notice（card_type/jump_list/card_action/\n sub_title/无 button_list/无 task_id/无「更新画像」hcl 行）；字段优先级 + 截断；缺项回落。
- `test_validate_card.py`：画像卡草稿 text_notice 放行；jump_list/card_action url==update_url 通过、幻觉 url 兜底；缺 card_action 注入；button_list 被删；兜底建 text_notice。
- 跑 `python3 -m unittest`（cp skill 全绿）。
- 本地冒烟：build_profile_card + validate_card 走一遍真数据。
- k3s 热补后发画像查询，确认卡片 text_notice 渲染 + 「查看完整画像」跳转 + sub_title \n 换行。

## 不在范围

- 选择卡（button_interaction）不改。
- 错误卡（button_interaction）不改。
- 试驾报告 skill 不改（仅参考其 text_notice 样式）。
- Langfuse/Hermes 插件（已另案处理，本次不碰）。
