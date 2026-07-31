---
name: im-channel-push
description: IM 通道消息推送（出站，支持企微/钉钉/飞书等，需主动推送时直接使用）。触发于"推送/提醒/通知/日报/定时"等意图——包括用户说"X时间提醒我..."时，建 cron 定时任务并在 cron prompt 里指定用本技能推送。不触发：用户在 IM 发消息来的入站对话回复（正常对话，gateway 自动透传，不需调本技能）。
---

# IM 通道消息推送（出站）

## 概述

Cron 定时任务 / 主动提醒场景下，向 IM 用户（企微/钉钉/飞书等）推送 markdown / text / 卡片消息。
通过 `terminal` 工具执行 `send.py`，由 gateway 调对应通道 API 下发。

与入站对话回复的区别：入站是用户发消息来、引擎回复（gateway 自动透传）；本技能是**引擎主动发起**的推送（Cron/事件触发），需显式调 `send.py` 经 gateway send 端点下发。

## 调用方式

**直接用 `terminal` 工具执行下面这条命令**（`find` 自动定位 send.py，只传参数；鉴权与 agent_id 由脚本从环境变量读，不要询问）：

```
python3 $(find /opt/data/skills -path "*im-channel-push/scripts/send.py" -type f | head -1) --touser <IM user_id> --msgtype markdown --content <消息内容>
```

> ⚠️ **直接执行这一条命令即可，不要：** 读 config.yaml、修改 SKILL.md 或 send.py、询问鉴权信息、用 `{{profile_skills_dir}}` 字面量（不会被替换，用上面的 `find` 命令定位脚本）。

## 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--touser` | 是 | 接收方 IM user_id（单聊）。群聊用 `--chat-id` 替代 |
| `--channel-type` | 否 | IM 通道：`wecom`（默认）/ `dingtalk` / `feishu` 等 |
| `--msgtype` | 否 | `markdown`（默认）/ `text` / `template_card` |
| `--content` | 是 | 消息内容。`template_card` 时为卡片 JSON 字符串 |
| `--chat-id` | 否 | 群聊 ID（与 `--touser` 二选一） |

## 消息类型选择

| 场景 | msgtype | 说明 |
|---|---|---|
| 日报/提醒正文 | `markdown` | 超长由 gateway 自动分段（2048 字节 chunk-flush） |
| 简短通知 | `text` | 纯文本 |
| 结构化卡片 | `template_card` | text_notice 列表卡等，content 为卡片 JSON（详见 `wecom-card设计.md`） |

## 示例

```bash
# 先定位脚本（一次即可，后续复用 $SM）
SM=$(find /opt/data/skills -path "*im-channel-push/scripts/send.py" -type f | head -1)

# 推送日报（markdown）
python3 $SM --touser LiuWei --msgtype markdown --content "## 今日客户跟进\n- 王先生：已试驾，意向高\n- 李女士：待回访"

# 简短通知（text）
python3 $SM --touser LiuWei --msgtype text --content "客户王先生已到店，请接待"
```

## 建 cron 定时提醒（重要）

当用户说"X时间提醒我Y"、"X时间通知我Y"等定时提醒请求时，**建 cron 任务必须关联本技能**（`--skill im-channel-push`），否则到点后提醒消息无法送达用户。

建 cron 时：
- **schedule**：用户说的时间（如"3分钟后"→`3m`，"每天9点"→`0 9 * * *`）
- **--skill**：`im-channel-push`（必须，触发时加载本技能的推送指令）
- **prompt**：写明推送目标和内容，如"向{user_id}推送提醒：Y"
- **--name**：简短任务名

示例（用户说"3分钟后提醒我接待客户"）：
```
hermes cron create "3m" "向LiuWei推送提醒：该接待客户了" --skill im-channel-push --name "提醒接待客户"
```

> ⚠️ **不关联 --skill im-channel-push 的 cron 提醒无法送达用户**。系统 delivery 在 IM 通道模式下不可用，必须通过本技能的 send.py 主动推送。

## 注意事项

1. **`--touser` 是 IM user_id**（如 `LiuWei`），不是客户姓名。推送目标必须是已绑定的销售/员工。
2. **不要询问 agent_id / 鉴权 key**：脚本从环境变量自动读取（`AGENT_ID` / `API_SERVER_KEY`），属平台配置，非销售应处理的问题。
3. **content 长度**：markdown 单条超 2048 字节由 gateway 自动分段，无需手动拆分。
4. **退出码**：0 成功；非 0 失败（1 参数/env 缺失；2 gateway 返回失败；3 网络错误）。失败时读 stderr 提示用户稍后重试或联系管理员。
5. **推送后用户回复走入站流程**：用户收到推送后回复，会进入正常入站对话（session_id 一致，上下文连续），不需再调本技能。
