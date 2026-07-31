"""Gateway 用户提示语集中管理（企微等 IM 渠道）。

按状态分级：初始化/处理中、临时故障（可重试）、配置问题（联系管理员）、用户操作（引导）。
统一 emoji 前缀 + 简洁一句。详见 docs/gateway提示信息优化设计.md。
"""

# === 初始化/处理中 ===
ENGINE_STARTING = "🤖 智能体启动中，请稍候... ⏳"
PROFILE_PREPARING = "🕐 正在准备会话环境，首次约需 15 秒，请稍候再对话..."

# === 临时故障（可重试）⚠️ ===
VOICE_RECOGNIZE_FAILED = "🎤 没听清，请重新发语音"
REPLY_FAILED = "⚠️ 回复失败，请稍后重试"
STREAM_INTERRUPTED = "⚠️ 回复生成中断，以上为部分内容"
CARD_RENDER_FAILED = "⚠️ 消息展示失败，请重试"
ATTACHMENT_FAILED = "📎 附件处理失败，请重试"
# 引擎流式返回 200 但 0 内容（LLM 首 token 前失败：401/500/timeout 等）→ 兜底提示，避免用户无响应
ENGINE_EMPTY_RESPONSE = "⚠️ 服务暂时无法响应，请稍后重试或联系管理员"

# === 配置问题（联系管理员）🛠️ ===
ENGINE_START_FAILED = "🛠️ 智能体启动异常，请联系管理员"
AGENT_UNAVAILABLE = "🛠️ 该智能体暂不可用，请联系管理员"
CARD_DEGRADED_TO_TEXT = "⚠️ 卡片展示异常，已转为文本"

# === 用户操作（引导）⚠️🚫 ===
NOT_BOUND = "⚠️ 您的企微账号尚未绑定，请联系管理员开通"
ACCESS_DENIED = "🚫 您暂无权限使用该智能体，请联系管理员添加用户组"
MESSAGE_FORMAT_INVALID = "⚠️ 消息格式异常，请重试"

# === 会话重置（用户自助命令 /重置会话、/reset、/清空会话）===
SESSION_RESET = "✅ 会话已重置，请重新提问"
SESSION_RESET_FAILED = "⚠️ 会话重置失败，请稍后重试"
