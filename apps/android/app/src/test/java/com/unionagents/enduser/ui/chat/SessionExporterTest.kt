package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.net.dto.Session
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验证 SessionExporter 的 JSON / Markdown 序列化：
 * - 字段名对齐 web 端 ChatPage.vue（session_id / title / model / messages / attachments）
 * - Markdown 转录含会话标题、role 标签、附件列表
 * - safeFilename 处理非法字符
 */
class SessionExporterTest {

    private val json = Json { ignoreUnknownKeys = true }

    private val session = Session(
        sessionId = "api_1784258806_67d793b1",
        id = "api_1784258806_67d793b1",
        title = "南京周边推荐",
        model = "deepseek-chat",
    )

    private val messages = listOf(
        Message(role = "user", content = "南京周边有什么推荐带小孩去历练的地方？"),
        Message(
            role = "assistant",
            content = "南京周边适合带小孩历练的去处有…",
            attachments = listOf(
                Attachment(
                    name = "风景.png",
                    path = "uploads/abc.png",
                    isImage = true,
                    mime = "image/png",
                    size = 102400,
                ),
            ),
        ),
        Message(role = "tool", content = "{\"output\":\"pong\"}"),
    )

    @Test
    fun `json contains session meta and messages`() {
        val out = SessionExporter.toJson(session, messages)
        val obj = json.parseToJsonElement(out).jsonObject
        assertEquals("api_1784258806_67d793b1", obj["session_id"]!!.jsonPrimitive.content)
        assertEquals("南京周边推荐", obj["title"]!!.jsonPrimitive.content)
        assertEquals("deepseek-chat", obj["model"]!!.jsonPrimitive.content)
        // exported_at 是时间戳，应能解析为 long
        assertTrue(obj["exported_at"]!!.jsonPrimitive.content.toLongOrNull() != null)
        assertEquals(3, obj["messages"]!!.jsonArray.size)
    }

    @Test
    fun `json message includes role content and attachments`() {
        val out = SessionExporter.toJson(session, messages)
        val obj = json.parseToJsonElement(out).jsonObject
        val assistant = obj["messages"]!!.jsonArray[1].jsonObject
        assertEquals("assistant", assistant["role"]!!.jsonPrimitive.content)
        assertEquals("南京周边适合带小孩历练的去处有…", assistant["content"]!!.jsonPrimitive.content)
        val atts = assistant["attachments"]!!.jsonArray
        assertEquals(1, atts.size)
        val att = atts[0].jsonObject
        assertEquals("风景.png", att["name"]!!.jsonPrimitive.content)
        assertEquals("uploads/abc.png", att["path"]!!.jsonPrimitive.content)
        assertTrue(att["is_image"]!!.jsonPrimitive.content.toBoolean())
        assertEquals("image/png", att["mime"]!!.jsonPrimitive.content)
    }

    @Test
    fun `json message without attachments omits attachments key`() {
        val out = SessionExporter.toJson(session, listOf(Message(role = "user", content = "hi")))
        val obj = json.parseToJsonElement(out).jsonObject
        val msg = obj["messages"]!!.jsonArray[0].jsonObject
        assertEquals("user", msg["role"]!!.jsonPrimitive.content)
        // 没 attachments 字段（不输出空数组）
        assertTrue(!msg.containsKey("attachments"))
    }

    @Test
    fun `transcript contains title role label and content`() {
        val out = SessionExporter.toTranscript(session, messages)
        assertTrue(out.contains("# 南京周边推荐"))
        assertTrue(out.contains("> model: deepseek-chat"))
        assertTrue(out.contains("## 用户"))
        assertTrue(out.contains("南京周边有什么推荐带小孩去历练的地方？"))
        assertTrue(out.contains("## 助手"))
        assertTrue(out.contains("南京周边适合带小孩历练的去处有…"))
        assertTrue(out.contains("[图] 风景.png"))
    }

    @Test
    fun `transcript falls back when title blank`() {
        val s = Session(sessionId = "abc", id = "abc")
        val out = SessionExporter.toTranscript(s, listOf(Message(role = "user", content = "ping")))
        assertTrue(out.contains("# abc"))
        assertTrue(out.contains("ping"))
    }

    @Test
    fun `safeFilename strips illegal chars`() {
        assertEquals("a_b_c", SessionExporter.safeFilename("a/b\\c", "fallback"))
        assertEquals("a_b", SessionExporter.safeFilename("a b", "fallback"))
        assertEquals("fallback", SessionExporter.safeFilename("", "fallback"))
        // 长标题截断到 40 字符
        val long = "一".repeat(50)
        assertEquals(40, SessionExporter.safeFilename(long, "fallback").length)
    }
}
