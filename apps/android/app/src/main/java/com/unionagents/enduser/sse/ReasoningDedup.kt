package com.unionagents.enduser.sse

/**
 * Hermes `reasoning.available` 去重判定。
 *
 * 引擎语义（实测 + 源码确认，api_server.py / conversation_loop.py）：
 * - 该事件每个"有文本的回合"结束后发一次，text = 该回合 assistant 正文（去标签，截断 500 字）。
 * - 对无独立推理的模型（如 deepseek-chat），text 就是本轮回复正文的重复，直接当"思考"
 *   展示会与回复撞内容。
 * - 多文本回合场景（先说一句→调工具→再回复）：累积流 = 回合1+回合2 拼接，回合2 的
 *   reasoning 是累积流的**后缀**而非前缀 —— 只做 startsWith 双向判断会漏掉它，
 *   导致思考卡在回复流完后瞬间弹出、内容与回复尾巴重复。
 *
 * 判定规则：reasoning 文本是累积流的子串（含相等/前缀/后缀），或累积流是 reasoning
 * 的前缀（流未收全时的部分重叠）→ 视为回复回声，丢弃。真正的独立推理（推理模型
 * think 块）与回复正文不同，不会被误杀。
 */
fun isReasoningEchoOfReply(reasoning: String, streamedReply: String): Boolean {
    val r = reasoning.trim()
    val s = streamedReply.trim()
    if (r.isEmpty() || s.isEmpty()) return false
    return s.contains(r) || r.startsWith(s)
}
