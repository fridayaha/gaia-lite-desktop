package com.unionagents.enduser.net

import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [buildAgentRequest]：Gateway 请求头注入。
 * X-Client-Type 无条件携带（gateway 据此把 Langfuse channel_type 记为 android），
 * X-Agent-ID 等仅在 AgentContext 已有值时注入。
 */
class AgentHeaderInterceptorTest {

    private val base = Request.Builder().url("http://gw.test/v1/runs").build()

    @Test
    fun `client type header always present without agent`() {
        val req = buildAgentRequest(base, AgentContext.State())
        assertEquals("android", req.header("X-Client-Type"))
        assertNull(req.header("X-Agent-ID"))
        assertNull(req.header("X-Session-ID"))
    }

    @Test
    fun `full state injects all headers`() {
        val req = buildAgentRequest(
            base,
            AgentContext.State(agentId = "a1", sessionId = "s1", engineType = "HERMES"),
        )
        assertEquals("android", req.header("X-Client-Type"))
        assertEquals("a1", req.header("X-Agent-ID"))
        assertEquals("s1", req.header("X-Session-ID"))
        assertEquals("HERMES", req.header("X-Engine-Type"))
    }

    @Test
    fun `agent without session omits session header`() {
        val req = buildAgentRequest(base, AgentContext.State(agentId = "a1"))
        assertEquals("a1", req.header("X-Agent-ID"))
        assertNull(req.header("X-Session-ID"))
    }
}
