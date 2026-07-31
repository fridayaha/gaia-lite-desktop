package com.unionagents.enduser.repo

import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.Message
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 服务端把 messages 存到 Hermes 前会把 attachments 字段剥掉、把路径嵌进 content 作为
 * ``[Attached files: ...]`` 提示。本测试验证 Android 端能从 content 还原结构化附件、
 * 并把 hint 从显示文本中剥掉。
 */
class AttachmentHintParserTest {

    @Test
    fun `restores single image attachment and strips hint`() {
        val msg = Message(
            id = 1L,
            role = "user",
            content = "这是什么图片？\n\n[Attached files: uploads/Screenshot_20260718_164211_com.larus.nova-1.jpg]",
        )
        val out = AttachmentHintParser.restore(msg)
        assertEquals("这是什么图片？", out.content)
        assertEquals(1, out.attachments.size)
        val att = out.attachments[0]
        assertEquals("uploads/Screenshot_20260718_164211_com.larus.nova-1.jpg", att.path)
        assertEquals("Screenshot_20260718_164211_com.larus.nova-1.jpg", att.name)
        assertTrue(att.isImage)
    }

    @Test
    fun `restores multiple attachments and strips hint`() {
        val msg = Message(
            id = 1L,
            role = "user",
            content = "看这两个\n\n[Attached files: uploads/a.png, uploads/b.pdf]",
        )
        val out = AttachmentHintParser.restore(msg)
        assertEquals("看这两个", out.content)
        assertEquals(2, out.attachments.size)
        assertTrue(out.attachments[0].isImage)
        assertFalse(out.attachments[1].isImage)
        assertEquals("b.pdf", out.attachments[1].name)
    }

    @Test
    fun `returns original message when no hint present`() {
        val msg = Message(
            id = 1L,
            role = "user",
            content = "普通文本，没有附件提示",
        )
        val out = AttachmentHintParser.restore(msg)
        assertEquals(msg.content, out.content)
        assertTrue(out.attachments.isEmpty())
    }

    @Test
    fun `returns original message when attachments already populated`() {
        val msg = Message(
            id = 1L,
            role = "user",
            content = "text\n\n[Attached files: uploads/a.jpg]",
            attachments = listOf(
                Attachment(name = "a.jpg", path = "uploads/a.jpg", isImage = true),
            ),
        )
        val out = AttachmentHintParser.restore(msg)
        // 已有结构化 attachments 时不动 content，避免重复处理
        assertEquals(msg.content, out.content)
        assertEquals(1, out.attachments.size)
    }

    @Test
    fun `returns original message when content is null`() {
        val msg = Message(id = 1L, role = "user", content = null)
        val out = AttachmentHintParser.restore(msg)
        assertEquals(null, out.content)
        assertTrue(out.attachments.isEmpty())
    }

    @Test
    fun `handles fallback content format where hint is the entire body`() {
        // 当用户只发附件不带文字时，gateway 合成 "I've uploaded N file(s): p1, p2" 作为 content。
        // 这种格式 hint 不在末尾方括号里，解析器不识别，原样返回（不会误删正文）。
        val msg = Message(
            id = 1L,
            role = "user",
            content = "I've uploaded 1 file(s): uploads/a.jpg",
        )
        val out = AttachmentHintParser.restore(msg)
        assertEquals(msg.content, out.content)
        assertTrue(out.attachments.isEmpty())
    }

    @Test
    fun `isImagePath recognizes common image extensions`() {
        assertTrue(AttachmentHintParser.isImagePath("uploads/a.jpg"))
        assertTrue(AttachmentHintParser.isImagePath("uploads/a.JPEG"))
        assertTrue(AttachmentHintParser.isImagePath("uploads/a.png"))
        assertTrue(AttachmentHintParser.isImagePath("uploads/a.webp"))
        assertFalse(AttachmentHintParser.isImagePath("uploads/a.pdf"))
        assertFalse(AttachmentHintParser.isImagePath("uploads/a.txt"))
    }
}
