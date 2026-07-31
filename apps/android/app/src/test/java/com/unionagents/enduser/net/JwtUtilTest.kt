package com.unionagents.enduser.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

class JwtUtilTest {

    private fun makeJwt(exp: Long): String {
        val header = """{"alg":"HS256","typ":"JWT"}"""
        val payload = """{"sub":"u1","exp":$exp}"""
        val enc = Base64.getUrlEncoder().withoutPadding()
        val h = enc.encodeToString(header.toByteArray())
        val p = enc.encodeToString(payload.toByteArray())
        return "$h.$p.sig"
    }

    @Test
    fun `parses exp from valid jwt`() {
        val exp = System.currentTimeMillis() / 1000 + 60
        val token = makeJwt(exp)
        assertEquals(exp, JwtUtil.parseExp(token))
    }

    @Test
    fun `returns null for non-jwt string`() {
        assertNull(JwtUtil.parseExp("not-a-jwt"))
    }

    @Test
    fun `returns null for token without exp field`() {
        val header = """{"alg":"HS256","typ":"JWT"}"""
        val payload = """{"sub":"u1"}"""
        val enc = Base64.getUrlEncoder().withoutPadding()
        val token = "${enc.encodeToString(header.toByteArray())}.${enc.encodeToString(payload.toByteArray())}.sig"
        assertNull(JwtUtil.parseExp(token))
    }

    @Test
    fun `returns null for two-part token`() {
        val token = "abc.def"
        assertNull(JwtUtil.parseExp(token))
    }

    @Test
    fun `returns null when payload is not valid base64`() {
        val token = "header.!!!.sig"
        assertNull(JwtUtil.parseExp(token))
    }

    @Test
    fun `returns null when payload is not valid json`() {
        val enc = Base64.getUrlEncoder().withoutPadding()
        val payload = enc.encodeToString("not json".toByteArray())
        val token = "header.$payload.sig"
        assertNull(JwtUtil.parseExp(token))
    }

    @Test
    fun `returns null for zero exp`() {
        val header = """{"alg":"HS256","typ":"JWT"}"""
        val payload = """{"sub":"u1","exp":0}"""
        val enc = Base64.getUrlEncoder().withoutPadding()
        val token = "${enc.encodeToString(header.toByteArray())}.${enc.encodeToString(payload.toByteArray())}.sig"
        assertNull(JwtUtil.parseExp(token))
    }
}
