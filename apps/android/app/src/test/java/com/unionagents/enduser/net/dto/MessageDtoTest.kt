package com.unionagents.enduser.net.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验证 Hermes engine 返回的会话/消息 JSON 能被 Android DTO 正确解析。
 *
 * 历史 bug：
 * - Hermes 消息体含 ``tool_calls: null`` 但 DTO 是 ``List<ToolCall> = emptyList()``（非空带默认）
 *   → 依赖 ``coerceInputValues = true`` 把 null 兜底成 emptyList，否则 SerializationException
 * - Hermes 会话列表/详情字段名与 OpenAI 不一致（``data`` vs ``sessions``、``id`` vs ``session_id``）
 *   → DTO 同时收两个字段，stable* 属性做兜底
 */
class MessageDtoTest {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        explicitNulls = false
        encodeDefaults = true
    }

    @Test
    fun `parses hermes session list with data field`() {
        val payload = """
            {"object":"list","data":[
                {"id":83,"role":"user","content":"你好","tool_calls":null},
                {"id":84,"role":"assistant","content":"你好！有什么可以帮你的吗？","tool_calls":null}
            ]}
        """.trimIndent()
        val resp = json.decodeFromString<MessageListResponse>(payload)
        val list = resp.data ?: resp.messages ?: emptyList()
        assertEquals(2, list.size)
        assertEquals(83L, list[0].id)
        assertEquals("你好", list[0].content)
        // tool_calls: null 应被 coerce 成 emptyList（不抛 SerializationException）
        assertEquals(emptyList<ToolCall>(), list[0].toolCalls)
    }

    @Test
    fun `parses hermes message with populated tool_calls`() {
        val payload = """
            {"id":84,"session_id":"s1","role":"assistant","content":"","tool_calls":[
                {"id":"call_1","call_id":"call_1","response_item_id":"fc_1","type":"function",
                 "function":{"name":"terminal","arguments":"{\"command\":\"ls\"}"}}
            ]}
        """.trimIndent()
        val resp = json.decodeFromString<MessageListResponse>(
            """{"data":[$payload]}"""
        )
        val msg = (resp.data ?: resp.messages ?: emptyList()).first()
        assertEquals("assistant", msg.role)
        assertEquals("", msg.content)
        assertEquals(1, msg.toolCalls.size)
        assertEquals("terminal", msg.toolCalls[0].function?.name)
        // 未知字段 call_id / response_item_id 被忽略
    }

    @Test
    fun `tool message filtered by isVisible`() {
        val payload = """
            {"data":[
                {"role":"user","content":"ping"},
                {"role":"tool","content":"{\"output\":\"pong\"}"}
            ]}
        """.trimIndent()
        val resp = json.decodeFromString<MessageListResponse>(payload)
        val list = (resp.data ?: resp.messages ?: emptyList()).filter { it.isVisible }
        assertEquals(1, list.size)
        assertEquals("user", list[0].role)
    }

    @Test
    fun `empty-content assistant with tool_calls is invisible`() {
        val payload = """
            {"data":[
                {"role":"assistant","content":"","tool_calls":[
                    {"id":"c1","type":"function","function":{"name":"terminal","arguments":"{}"}}
                ]}
            ]}
        """.trimIndent()
        val resp = json.decodeFromString<MessageListResponse>(payload)
        val visible = (resp.data ?: resp.messages ?: emptyList()).filter { it.isVisible }
        assertTrue(visible.isEmpty())
    }

    @Test
    fun `session list parses hermes session with id field`() {
        val payload = """
            {"data":[
                {"id":"api_1784126667_f35ed4fc","source":"api_server","model":"hermes-agent",
                 "started_at":1784126667.0,"last_active":1784126667.0,"message_count":6}
            ]}
        """.trimIndent()
        val resp = json.decodeFromString<SessionListResponse>(payload)
        val list = resp.data ?: resp.sessions ?: emptyList()
        assertEquals(1, list.size)
        assertEquals("api_1784126667_f35ed4fc", list[0].id)
        // stableId 兜底：sessionId 为空 → 用 id
        assertEquals("api_1784126667_f35ed4fc", list[0].stableId)
        // stableLastAt 兜底：last_message_at 缺失 → 用 startedAt
        assertEquals(1784126667.0, list[0].stableLastAt ?: 0.0, 0.001)
    }

    @Test
    fun `start run request serializes conversation_history and user`() {
        // Hermes run 无状态：必须把上一轮 user/assistant 消息塞进 conversation_history，
        // 否则引擎看不到上文。验证 JSON 字段名 + 顺序 + 本轮 input 不混入 history。
        val req = StartRunRequest(
            session_id = "api_1784258806_67d793b1",
            input = "就上面那个带小孩历练的事情",
            model = "deepseek-chat",
            conversationHistory = listOf(
                HistoryItem(role = "user", content = "南京周边有什么推荐带小孩去历练的地方？"),
                HistoryItem(role = "assistant", content = "南京周边适合带小孩历练的去处有…"),
            ),
            user = "29000329-f405-44d0-a632-6eded40fc419",
        )
        val encoded = json.encodeToString(StartRunRequest.serializer(), req)
        // 字段名按 SerialName 序列化，user 走默认名
        assertTrue(encoded.contains("\"session_id\":\"api_1784258806_67d793b1\""))
        assertTrue(encoded.contains("\"input\":\"就上面那个带小孩历练的事情\""))
        assertTrue(encoded.contains("\"model\":\"deepseek-chat\""))
        assertTrue(encoded.contains("\"conversation_history\":["))
        assertTrue(encoded.contains("\"role\":\"user\""))
        assertTrue(encoded.contains("\"role\":\"assistant\""))
        assertTrue(encoded.contains("\"user\":\"29000329-f405-44d0-a632-6eded40fc419\""))
        // decode 往返
        val decoded = json.decodeFromString(StartRunRequest.serializer(), encoded)
        assertEquals(2, decoded.conversationHistory.size)
        assertEquals("user", decoded.conversationHistory[0].role)
        assertEquals("南京周边有什么推荐带小孩去历练的地方？", decoded.conversationHistory[0].content)
    }

    @Test
    fun `live intermediate snapshot fields are not serialized`() {
        // liveThinking/liveToolCalls/runId 是客户端侧快照（TUI 顺序渲染/反馈锚点），
        // 标了 @Transient：导出/上行 JSON 不应带这些字段
        val msg = Message(
            id = 1L,
            role = "assistant",
            content = "回复",
            runId = "run-1",
            liveThinking = "先思考一下",
            liveToolCalls = listOf(ToolCallState("terminal", "ls", "tc-1", completed = true, error = null)),
        )
        val encoded = json.encodeToString(Message.serializer(), msg)
        assertTrue(!encoded.contains("liveThinking"))
        assertTrue(!encoded.contains("liveToolCalls"))
        assertTrue(!encoded.contains("runId"))
        assertTrue(!encoded.contains("先思考一下"))
        // 网络字段照常序列化
        assertTrue(encoded.contains("\"role\":\"assistant\""))
        assertTrue(encoded.contains("\"content\":\"回复\""))
    }
}
