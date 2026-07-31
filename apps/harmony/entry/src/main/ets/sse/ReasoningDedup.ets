/**
 * Hermes `reasoning.available` 去重判定。复刻 Android sse/ReasoningDedup.kt。
 *
 * 引擎语义：reasoning.available 每回合发一次，text = 该回合 assistant 正文（截断 500 字）。
 * - 无独立推理的模型（如 deepseek-chat）：text 是本轮回复正文的重复 → 当思考展示会撞内容
 * - 多文本回合场景（先说一句→调工具→再回复）：累积流 = 回合1+回合2 拼接，回合2 的 reasoning
 *   是累积流的**后缀**而非前缀 —— 只做 startsWith 双向判断会漏掉它。
 *
 * 判定规则：reasoning 是累积流的子串（含相等/前缀/后缀），或累积流是 reasoning 的前缀
 * → 视为回复回声，丢弃。真正的独立推理（推理模型 think 块）与回复正文不同，不会被误杀。
 */
export function isReasoningEchoOfReply(reasoning: string, streamedReply: string): boolean {
  const r = reasoning.trim()
  const s = streamedReply.trim()
  if (r.length === 0 || s.length === 0) return false
  return s.includes(r) || r.startsWith(s)
}
