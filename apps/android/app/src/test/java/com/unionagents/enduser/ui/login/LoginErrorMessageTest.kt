package com.unionagents.enduser.ui.login

import org.junit.Assert.assertEquals
import org.junit.Test

class LoginErrorMessageTest {

    @Test
    fun `invalid_credentials maps to password error`() {
        assertEquals("用户名或密码错误", errorMessageFromDetail("invalid_credentials"))
    }

    @Test
    fun `captcha_required maps to retry hint`() {
        assertEquals(
            "登录失败次数过多，请稍后再试或联系管理员重置",
            errorMessageFromDetail("captcha_required"),
        )
    }

    @Test
    fun `captcha_invalid maps to captcha error`() {
        assertEquals("图形验证码错误或已过期", errorMessageFromDetail("captcha_invalid"))
    }

    @Test
    fun `account_locked maps to locked message`() {
        assertEquals("账号已被锁定，请稍后再试", errorMessageFromDetail("account_locked"))
    }

    @Test
    fun `unknown detail falls back to generic`() {
        assertEquals("请求失败，请重试", errorMessageFromDetail("something_unexpected"))
    }

    @Test
    fun `null detail falls back to generic`() {
        assertEquals("请求失败，请重试", errorMessageFromDetail(null))
    }

    @Test
    fun `blank detail falls back to generic`() {
        assertEquals("请求失败，请重试", errorMessageFromDetail(""))
    }
}
