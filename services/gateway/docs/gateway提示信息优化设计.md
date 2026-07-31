# Gateway 企微提示信息优化设计

> 代码：`services/gateway/app/channel/{dispatcher,wecom}.py`、`profile_resolver.py`
> 关联待办 [[gateway-ux-prompt-optimization]]、[[asr-sidecar-build-deploy]]（profile 冷启动 110s 实测）
> 2026-07

## 目标
梳理 gateway 处理消息的所有状态分支，统一企微用户界面的提示信息（初始化/处理中/各报错），补全 profile gateway 冷启动提示（实测 110s 静默），按"临时故障可重试 / 配置问题联系管理员 / 用户操作引导"分级，提升体验。

## 一、现状：dispatcher 处理流程与提示点

```
用户消息进 dispatcher._process_one
  │
  ├─ Step 0 权限闸门 (_check_im_access)
  │    ├─ NotBound        → "⚠️ 您尚未绑定 UnionAgents 账号，请先在 UnionAgents 平台完成 IM 账号绑定..."  ← 冗长
  │    ├─ AccessDenied    → "🚫 您没有访问该智能体的权限，请联系管理员申请权限。"
  │    └─ ProfileNotFound → "😔 该智能体暂不可用，请联系管理员。"
  │
  ├─ Step 0.5 语音转录 (VOICE)
  │    └─ transcribe 失败 → "语音识别失败，请重试"  ← 无 emoji、干
  │
  ├─ Step 1 ensure_engine_ready (engine pod health)
  │    ├─ not ready       → "😔 智能体启动失败，请联系管理员"
  │    └─ ready (was_already_running?)
  │
  ├─ Step 3 冷启动提示 (仅 !was_already_running)
  │    └─ send_processing → "🤖 正在启动智能体引擎，请稍候... ⏳"
  │       (send_processing_done 企微跳过 ✅ 更新)
  │
  ├─ Step 4-5 session + _resolve_profile
  │    └─ profile gateway 冷启动 110s 无提示  ← 最大缺口
  │
  ├─ Step 7 流式转发
  │    ├─ 首个 chunk     → 发回复卡（冷启动时更新启动卡 ✅，企微跳过）
  │    ├─ 流式中断       → "⚠️ 回复生成中断，以上为部分内容"
  │    └─ 回复失败       → "😔 智能体回复失败，请重试"
  │
  └─ 卡片相关 (wecom.py)
       ├─ 渲染失败       → "消息渲染失败，请重试"
       └─ 格式异常       → "消息格式异常，请重试"
```

## 二、问题分析

| 问题 | 现状 | 影响 |
|---|---|---|
| ① profile gateway 冷启动无提示 | Step 1 只查 engine pod health（200→was_already_running=True），Step 3 冷启动提示被跳过；但 _resolve_profile 阶段 profile 冷启动 110s 期间用户无反馈 | 用户干等 ~110s，以为卡死 |
| ② 提示语风格不统一 | 有的带 emoji（😔🤖⚠️🚫），有的不带；emoji 语义重叠（😔 既表启动失败又表回复失败又表不可用） | 视觉杂乱，难区分状态 |
| ③ 错误分级不清 | 临时故障（回复失败/渲染失败）与配置问题（启动失败/不可用）混用"请重试"或"请联系管理员" | 用户不知该重试还是找管理员 |
| ④ 语音失败提示干 | "语音识别失败，请重试"——无 emoji、无场景化 | 体验冷冰冰 |
| ⑤ 未绑定提示冗长 | "请先在 UnionAgents 平台完成 IM 账号绑定..."——企微用户未必有平台账号 | 引导路径长 |
| ⑥ 卡片渲染失败未区分降级 | 渲染失败时若已降级发文本，仍提示"请重试" | 用户重复操作 |
| ⑦ 热启动长处理无反馈 | profile 冷启 110s / skill 执行长时，无"处理中"反馈 | 用户以为卡死 |

## 三、新设计

### 设计原则
1. **统一 emoji 前缀**：每个状态类一个语义 emoji（🤖 启动 / 🕐 准备 / 🎤 语音 / ⚠️ 临时故障 / 🛠️ 配置异常 / 🚫 无权限），不再用 😔 模糊表情。
2. **简洁一句**：状态 + 动作（等待/重试/联系管理员），不超过 25 字。
3. **分级**：①初始化/处理中 ②临时故障（可重试）③配置问题（联系管理员）④用户操作（引导）。
4. **企微限制适配**：企微不能编辑消息、无"正在输入"API；处理中反馈用"占位消息"，内容到达即就绪，不发多余 ✅。
5. **profile 冷启动提示**：补全，检测 profile 未就绪时发"准备会话环境"。
6. **~~超时补发"思考中"~~**（已去掉）：企微撤回会留痕迹，留着又干扰，故去掉。用户等待正式回复即可。

### 提示语总表

| 分级 | 状态 | 现有提示 | 新提示 | 触发点 |
|---|---|---|---|---|
| **初始化/处理中** 🤖🕐🤔 | engine pod 冷启动 | 🤖 正在启动智能体引擎，请稍候... ⏳ | 🤖 智能体启动中，请稍候... ⏳ | dispatcher Step 3 (send_processing) |
| | profile gateway 冷启动（新增） | （无，静默 110s） | 🕐 正在准备会话环境，首次约需 15 秒，请稍候再对话... | _resolve_profile 检测 profile 冷启动 |
| | ~~热启动长处理（首 chunk >5s）~~ | （无） | ~~🤔 思考中...~~（已去掉） | ~~dispatcher 超时定时器补发~~（已移除） |
| **临时故障**（可重试）⚠️ | 语音识别失败 | 语音识别失败，请重试 | 🎤 没听清，请重新发语音 | dispatcher Step 0.5 |
| | 回复失败 | 😔 智能体回复失败，请重试 | ⚠️ 回复失败，请稍后重试 | dispatcher _process_one_streaming |
| | 流式中断 | ⚠️ 回复生成中断，以上为部分内容 | ⚠️ 回复生成中断，以上为部分内容 | （保留） |
| | 卡片渲染失败（未降级） | 消息渲染失败，请重试 | ⚠️ 消息展示失败，请重试 | wecom send_card_message |
| **配置问题**（联系管理员）🛠️ | engine 启动失败 | 😔 智能体启动失败，请联系管理员 | 🛠️ 智能体启动异常，请联系管理员 | dispatcher Step 1 / _process_card_click |
| | 智能体不可用 (ProfileNotFound) | 😔 该智能体暂不可用，请联系管理员。 | 🛠️ 该智能体暂不可用，请联系管理员 | _check_im_access |
| | ~~卡片渲染失败（原设计"已降级文本"分支）~~ | — | 不存在此分支 | 实际不降级：errcode≠0 统一发 CARD_RENDER_FAILED（见上行临时故障），不尝试降级发原文本；CARD_DEGRADED_TO_TEXT 常量已定义但未使用（预留） |
| **用户操作**（引导）⚠️🚫 | 未绑定 (NotBound) | ⚠️ 您尚未绑定 UnionAgents 账号，请先在 UnionAgents 平台完成 IM 账号绑定后再使用。如有疑问请联系管理员。 | ⚠️ 您的企微账号尚未绑定，请联系管理员开通 | _check_im_access |
| | 无权限 (AccessDenied) | 🚫 您没有访问该智能体的权限，请联系管理员申请权限。 | 🚫 您暂无权限使用该智能体，请联系管理员添加用户组 | _check_im_access |
| | 消息格式异常 | 消息格式异常，请重试 | ⚠️ 消息格式异常，请重试 | wecom send_message |

### emoji 语义约定

| emoji | 语义 | 用于 |
|---|---|---|
| 🤖 | 智能体引擎启动 | engine pod 冷启动 |
| 🕐 | 会话环境准备 | profile gateway 冷启动（新增） |
| ~~🤔~~ | ~~思考中~~ | ~~热启动长处理超时补发~~（已去掉） |
| 🎤 | 语音相关 | 语音识别失败 |
| ⚠️ | 临时故障/可重试 | 回复失败、流式中断、渲染失败、格式异常 |
| 🛠️ | 配置异常/需管理员 | 启动失败、智能体不可用 |
| 🚫 | 无权限 | AccessDenied |

## 四、~~超时补发"思考中"~~（已去掉）

原设计：首 chunk >5s 补发"🤔 思考中..."占位。

去掉原因：企微撤回会留"应用撤回了一条消息"痕迹（比思考中更干扰），占位消息保留又影响阅读体验。故移除 THINKING 常量、PROCESSING_HINT_TIMEOUT 常量、dispatcher 超时定时器逻辑。用户等待正式回复即可。

## 五、绑定提示（确认简洁版）

manager `im_bindings.py` 权限：平台管理员可管任意用户绑定，组用户可绑自己或同组成员。但未绑定的企微用户通常**没有 UnionAgents 平台账号**（没账号无法登录 console 自己绑），实际流程是管理员创建账号 + 绑定 IM。

所以未绑定提示用简洁版："⚠️ 您的企微账号尚未绑定，请联系管理员开通"。

## 六、实现要点

1. **新增 `app/messages.py`**：集中提示常量（按上表新提示），避免散落 dispatcher/wecom，便于后续 i18n / 统一改。
2. **profile 冷启动提示**：`_resolve_profile` 返回 `(profile_name, was_cold)`（`profile_resolver.resolve` 设 `was_cold`：cached_port 无 or force_ensure = 冷启动）。dispatcher 在 profile 冷启动**且 engine 热启动**时发"🕐 正在准备会话环境..."（条件 `profile_was_cold and was_already_running and not processing_msg_id`）。engine 冷启动时已有 send_processing "🤖 启动中"，不发 PROFILE_PREPARING（避免重复）。
3. **~~超时补发"思考中"~~**（已去掉）：移除 THINKING/PROCESSING_HINT_TIMEOUT 常量 + dispatcher 超时定时器。企微撤回留痕迹，占位消息影响体验。
4. **统一提示语**：`dispatcher.py` 已全面改用 `messages.py` 常量；`wecom.py` 部分使用（3 个常量）；`feishu.py`/`dingtalk.py` 未接入 `messages.py`，仍用硬编码文案（如 `feishu.py` 第 347 行仍为旧文案"🤖 正在启动智能体引擎，请稍候... ⏳"，新版应为"🤖 智能体启动中，请稍候... ⏳"），待后续统一。
5. **卡片渲染失败统一不降级**：`wecom.send_card_message` 渲染失败（errcode≠0）时统一发 `CARD_RENDER_FAILED`（"⚠️ 消息展示失败，请重试"），不尝试降级 `send_message` 发原文本。`CARD_DEGRADED_TO_TEXT` 常量已在 `messages.py` 中定义但未使用（预留）。
6. **不做的**：不发"✅ 引擎已就绪"（企微不能编辑，内容到达即就绪，多发是噪音）；不所有消息都发占位（快速回复不打扰）。

## 七、确认结论

1. profile 冷启动提示：engine 冷时已有 send_processing "🤖 启动中"，profile 冷启动提示仅在 engine 热时发 PROFILE_PREPARING（条件 `profile_was_cold and was_already_running and not processing_msg_id`，避免重复）。✅
2. 提示语集中：新建 `app/messages.py`。✅
3. emoji 风格：🤖🕐🎤⚠️🛠️🚫（🤔 思考中已去掉）。✅
4. 未绑定提示：简洁版"请联系管理员开通"。✅
5. ~~超时补发"思考中"~~：已去掉（企微撤回留痕迹）。
