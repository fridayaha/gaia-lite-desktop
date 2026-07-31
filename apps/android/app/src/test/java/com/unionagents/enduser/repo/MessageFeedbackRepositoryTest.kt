package com.unionagents.enduser.repo

import com.unionagents.enduser.net.dto.Message
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * messageRefOf 锚点逻辑：
 * - 引擎历史消息带稳定自增 id（远小于 1e12）→ "mid:{id}"
 * - 本地 user 消息 id 是 currentTimeMillis 占位（≥1e12）→ hash 兜底
 * - 流式刚完成 / 引擎无 id 的消息（id=null）→ hash 兜底
 * - hash 锚点对同内容稳定、对不同内容区分
 */
class MessageFeedbackRepositoryTest {

    @Test
    fun `engine message id uses mid anchor`() {
        val msg = Message(id = 226L, role = "assistant", content = "你好")
        assertEquals("mid:226", MessageFeedbackRepository.messageRefOf(msg))
    }

    @Test
    fun `local placeholder id falls back to hash`() {
        // System.currentTimeMillis() 量级（≥1e12）视为本地占位，不作引擎锚点
        val msg = Message(id = 1_780_000_000_000L, role = "user", content = "本地上行消息")
        val ref = MessageFeedbackRepository.messageRefOf(msg)
        assertTrue(ref.startsWith("hash:"))
        assertEquals("hash:".length + 16, ref.length)
    }

    @Test
    fun `null id falls back to hash`() {
        val msg = Message(id = null, role = "assistant", content = "流式刚完成的回复")
        assertTrue(MessageFeedbackRepository.messageRefOf(msg).startsWith("hash:"))
    }

    @Test
    fun `hash anchor is stable for same content`() {
        val a = Message(role = "assistant", content = "同一段内容")
        val b = Message(role = "assistant", content = "同一段内容")
        assertEquals(MessageFeedbackRepository.messageRefOf(a), MessageFeedbackRepository.messageRefOf(b))
    }

    @Test
    fun `hash anchor differs for different content`() {
        val a = Message(role = "assistant", content = "内容 A")
        val b = Message(role = "assistant", content = "内容 B")
        assertNotEquals(MessageFeedbackRepository.messageRefOf(a), MessageFeedbackRepository.messageRefOf(b))
    }

    @Test
    fun `null content hashes consistently with empty content`() {
        val a = Message(role = "assistant", content = null)
        val b = Message(role = "assistant", content = "")
        assertEquals(MessageFeedbackRepository.messageRefOf(a), MessageFeedbackRepository.messageRefOf(b))
    }
}
