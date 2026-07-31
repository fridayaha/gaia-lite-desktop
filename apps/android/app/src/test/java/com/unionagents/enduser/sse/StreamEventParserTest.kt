package com.unionagents.enduser.sse

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamEventParserTest {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        explicitNulls = false
    }

    // ── 非 HERMES：OpenAI chat-completions 帧 ──

    @Test
    fun `parses non-hermes content delta`() {
        val payload = """{"choices":[{"delta":{"content":"hello"}}]}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ContentDelta)
        assertEquals("hello", (ev as StreamEvent.ContentDelta).text)
    }

    @Test
    fun `parses non-hermes reasoning_content delta`() {
        val payload = """{"choices":[{"delta":{"reasoning_content":"thinking..."}}]}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ReasoningDelta)
        assertEquals("thinking...", (ev as StreamEvent.ReasoningDelta).text)
    }

    @Test
    fun `parses non-hermes reasoning field as fallback`() {
        val payload = """{"choices":[{"delta":{"reasoning":"alt reasoning"}}]}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ReasoningDelta)
        assertEquals("alt reasoning", (ev as StreamEvent.ReasoningDelta).text)
    }

    @Test
    fun `parses non-hermes usage as completed`() {
        val payload = """{"usage":{"prompt_tokens":10,"completion_tokens":5}}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.Completed)
    }

    @Test
    fun `parses non-hermes tool running event`() {
        val payload = """{"tool":"read_file","status":"running","label":"读取 README","toolCallId":"call-1"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolStarted)
        val t = ev as StreamEvent.ToolStarted
        assertEquals("read_file", t.name)
        assertEquals("读取 README", t.preview)
        assertEquals("call-1", t.toolCallId)
    }

    @Test
    fun `parses non-hermes tool completed event`() {
        val payload = """{"tool":"read_file","status":"completed"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertEquals("read_file", (ev as StreamEvent.ToolCompleted).name)
    }

    @Test
    fun `parses non-hermes tool completed with boolean true error`() {
        // 非 Hermes 引擎若发 boolean true 错误标志 → 占位文案（与 Hermes 分支行为一致）
        val payload = """{"tool":"read_file","status":"completed","error":true}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertEquals("工具执行失败", (ev as StreamEvent.ToolCompleted).error)
    }

    @Test
    fun `parses non-hermes tool completed with boolean false error`() {
        // 非 Hermes 引擎若发 boolean false → 成功（null）
        val payload = """{"tool":"write_file","status":"completed","error":false}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertNull((ev as StreamEvent.ToolCompleted).error)
    }

    @Test
    fun `parses non-hermes tool completed with string error`() {
        // 非 Hermes 引擎发字符串错误信息 → 原样保留
        val payload = """{"tool":"read_file","status":"completed","error":"File not found"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertEquals("File not found", (ev as StreamEvent.ToolCompleted).error)
    }

    @Test
    fun `parses non-hermes run start with run_id`() {
        val payload = """{"type":"run.start","run_id":"r-abc"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.RunStarted)
        assertEquals("r-abc", (ev as StreamEvent.RunStarted).runId)
    }

    @Test
    fun `parses non-hermes approval request`() {
        val payload = """{"type":"approval.request","run_id":"r-1","command":"rm -rf /","description":"删除根目录","choices":["once","session","always","deny"]}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ApprovalRequested)
        val a = ev as StreamEvent.ApprovalRequested
        assertEquals("r-1", a.runId)
        assertEquals("rm -rf /", a.command)
        assertEquals("删除根目录", a.description)
        assertEquals(listOf("once", "session", "always", "deny"), a.choices)
    }

    @Test
    fun `parses non-hermes approval responded`() {
        val payload = """{"type":"approval.responded","choice":"once"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ApprovalResponded)
        assertEquals("once", (ev as StreamEvent.ApprovalResponded).choice)
    }

    @Test
    fun `returns null on invalid json`() {
        val ev = StreamEventParser.parseNonHermes("not json", json)
        assertNull(ev)
    }

    // ── HERMES：event 字段路由 ──

    @Test
    fun `parses hermes run started`() {
        val payload = """{"event":"run.started","run_id":"r-1"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.RunStarted)
        assertEquals("r-1", (ev as StreamEvent.RunStarted).runId)
    }

    @Test
    fun `parses hermes message delta`() {
        val payload = """{"event":"message.delta","delta":"hello world"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.HermesDelta)
        assertEquals("hello world", (ev as StreamEvent.HermesDelta).delta)
    }

    @Test
    fun `returns null on empty hermes delta`() {
        val payload = """{"event":"message.delta","delta":""}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertNull(ev)
    }

    @Test
    fun `parses hermes reasoning available`() {
        val payload = """{"event":"reasoning.available","text":"思考中..."}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.HermesReasoning)
        assertEquals("思考中...", (ev as StreamEvent.HermesReasoning).text)
    }

    @Test
    fun `parses hermes tool started`() {
        val payload = """{"event":"tool.started","tool":"grep","preview":"搜索文件","toolCallId":"t-1"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolStarted)
        val t = ev as StreamEvent.ToolStarted
        assertEquals("grep", t.name)
        assertEquals("搜索文件", t.preview)
        assertEquals("t-1", t.toolCallId)
    }

    @Test
    fun `parses hermes tool completed with error`() {
        val payload = """{"event":"tool.completed","tool":"grep","error":"no match"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("grep", t.name)
        assertEquals("no match", t.error)
    }

    @Test
    fun `parses hermes tool completed without error`() {
        val payload = """{"event":"tool.completed","tool":"grep"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertNull((ev as StreamEvent.ToolCompleted).error)
    }

    @Test
    fun `parses hermes tool completed with boolean true error`() {
        // Hermes 实际协议：error 是 is_error 布尔标志，true=失败
        // SSE 不带实际错误信息，parser 应返回占位文案而非 "true" 字符串
        val payload = """{"event":"tool.completed","tool":"read_file","error":true}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("read_file", t.name)
        assertEquals("工具执行失败", t.error)
    }

    @Test
    fun `parses hermes tool completed with boolean false error`() {
        // Hermes 实际协议：error=false 表示成功，应解析为 null（不能显示成 "false" 错误）
        val payload = """{"event":"tool.completed","tool":"write_file","error":false}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("write_file", t.name)
        assertNull(t.error)
    }

    @Test
    fun `parses hermes tool completed with null error`() {
        // error: null 应解析为 null（成功）
        val payload = """{"event":"tool.completed","tool":"grep","error":null}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        assertNull((ev as StreamEvent.ToolCompleted).error)
    }

    @Test
    fun `parses hermes tool completed with result field`() {
        val payload = """{"event":"tool.completed","tool":"terminal","result":"total 0\ndrwxr-xr-x 2 root root 40 Jul 17 15:00 ."}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("terminal", t.name)
        assertNull(t.error)
        assertTrue(t.result?.contains("total 0") == true)
    }

    @Test
    fun `parses hermes tool completed with output field as result fallback`() {
        // 引擎可能用 output 字段名而非 result，需要双兜底
        val payload = """{"event":"tool.completed","tool":"execute_code","output":"42"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("execute_code", t.name)
        assertEquals("42", t.result)
    }

    @Test
    fun `parses non-hermes tool completed with result field`() {
        val payload = """{"tool":"terminal","status":"completed","result":"file1.txt\nfile2.txt"}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.ToolCompleted)
        val t = ev as StreamEvent.ToolCompleted
        assertEquals("terminal", t.name)
        assertEquals("file1.txt\nfile2.txt", t.result)
        assertNull(t.error)
    }

    @Test
    fun `parses hermes approval request with default choices`() {
        val payload = """{"event":"approval.request","run_id":"r-1","command":"rm","description":"删除"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ApprovalRequested)
        val a = ev as StreamEvent.ApprovalRequested
        assertEquals(listOf("once", "session", "always", "deny"), a.choices)
    }

    @Test
    fun `parses hermes approval responded`() {
        val payload = """{"event":"approval.responded","choice":"deny"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.ApprovalResponded)
        assertEquals("deny", (ev as StreamEvent.ApprovalResponded).choice)
    }

    @Test
    fun `parses hermes run completed with output`() {
        val payload = """{"event":"run.completed","output":"done","usage":{"prompt_tokens":1}}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.Completed)
        assertEquals("done", (ev as StreamEvent.Completed).output)
    }

    @Test
    fun `parses hermes run failed`() {
        val payload = """{"event":"run.failed","error":"LLM provider not configured"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.Failed)
        assertEquals("LLM provider not configured", (ev as StreamEvent.Failed).error)
    }

    @Test
    fun `parses hermes run cancelled`() {
        val payload = """{"event":"run.cancelled"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.Cancelled)
    }

    @Test
    fun `returns null on unknown hermes event`() {
        val payload = """{"event":"unknown.event"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertNull(ev)
    }

    // ── gateway 静默看门狗提示帧 ──

    @Test
    fun `parses hermes gateway silence hint`() {
        val payload = """{"event":"gateway.silence","elapsed":16}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.SilenceHint)
        assertEquals(16, (ev as StreamEvent.SilenceHint).elapsedSeconds)
    }

    @Test
    fun `parses non-hermes gateway silence hint`() {
        val payload = """{"event":"gateway.silence","elapsed":8}"""
        val ev = StreamEventParser.parseNonHermes(payload, json)
        assertTrue(ev is StreamEvent.SilenceHint)
        assertEquals(8, (ev as StreamEvent.SilenceHint).elapsedSeconds)
    }

    @Test
    fun `silence hint defaults elapsed to zero when missing or malformed`() {
        val missing = StreamEventParser.parseHermes("""{"event":"gateway.silence"}""", json)
        assertEquals(0, (missing as StreamEvent.SilenceHint).elapsedSeconds)
        val malformed = StreamEventParser.parseHermes("""{"event":"gateway.silence","elapsed":"soon"}""", json)
        assertEquals(0, (malformed as StreamEvent.SilenceHint).elapsedSeconds)
    }

    @Test
    fun `silence hint parse does not swallow other events`() {
        // 提示帧识别必须严格匹配 event 字段，不得影响常规帧
        val payload = """{"event":"message.delta","delta":"hi"}"""
        val ev = StreamEventParser.parseHermes(payload, json)
        assertTrue(ev is StreamEvent.HermesDelta)
    }
}
