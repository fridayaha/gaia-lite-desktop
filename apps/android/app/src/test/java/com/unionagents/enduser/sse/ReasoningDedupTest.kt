package com.unionagents.enduser.sse

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验证 reasoning.available 与回复正文的去重判定。
 *
 * 引擎行为（直连引擎 SSE 实测）：
 * - 每个有文本的回合结束后推一次 reasoning.available，text = 该回合回复正文（截断 500 字）
 * - 多文本回合时累积流 = 回合1+回合2 拼接，回合2 的 reasoning 是累积流的**后缀**
 * - 无独立推理的模型（deepseek-chat）reasoning 与回复完全同源 → 必须全部判为回声
 */
class ReasoningDedupTest {

    @Test
    fun `identical text is echo`() {
        assertTrue(isReasoningEchoOfReply("当前目录是空的。", "当前目录是空的。"))
    }

    @Test
    fun `reasoning as prefix of reply is echo`() {
        // 单回合：reasoning 截断 500 字，回复比它长
        val reply = "当前目录是 /root，目录是空的，没有任何文件。"
        assertTrue(isReasoningEchoOfReply(reply.take(20), reply))
    }

    @Test
    fun `reasoning as suffix of accumulated stream is echo`() {
        // 多文本回合（先说一句→调工具→再回复）：回合2 reasoning 是累积流的后缀。
        // 旧实现只做 startsWith 双向判断会漏掉 → 思考卡在回复流完后弹出、内容重复。
        val turn1 = "我先查看一下当前目录的内容。"
        val turn2 = "当前目录是空的。"
        val accumulated = turn1 + "\n\n" + turn2
        assertTrue(isReasoningEchoOfReply(turn2, accumulated))
        assertTrue(isReasoningEchoOfReply(turn1, accumulated))
    }

    @Test
    fun `truncated reasoning contained mid-stream is echo`() {
        // 长回复：reasoning = 回复前 500 字，累积流含完整回复 → 子串判定覆盖
        val full = "先说一句。" + "很长的正文".repeat(200)
        val blob = full.take(500)
        assertTrue(isReasoningEchoOfReply(blob, full + "结尾补充"))
    }

    @Test
    fun `reply prefix of reasoning is echo`() {
        // 流未收全时 reasoning 已含完整回复（部分重叠的防御分支）
        assertTrue(isReasoningEchoOfReply("当前目录是空的，没有任何文件。", "当前目录是空的"))
    }

    @Test
    fun `genuinely different reasoning is kept`() {
        // 推理模型的独立思考内容与回复正文不同 → 不能误杀
        val reasoning = "用户想看目录内容，我应该调用 terminal 工具执行 ls。"
        val reply = "当前目录是空的。"
        assertFalse(isReasoningEchoOfReply(reasoning, reply))
    }

    @Test
    fun `empty inputs are kept`() {
        assertFalse(isReasoningEchoOfReply("", "回复"))
        assertFalse(isReasoningEchoOfReply("思考", ""))
        assertFalse(isReasoningEchoOfReply("", ""))
    }

    @Test
    fun `whitespace is trimmed before comparison`() {
        assertTrue(isReasoningEchoOfReply("  当前目录是空的。\n", "当前目录是空的。"))
    }
}
