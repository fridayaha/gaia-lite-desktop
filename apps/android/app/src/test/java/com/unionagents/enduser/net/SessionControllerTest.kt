package com.unionagents.enduser.net

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class SessionControllerTest {

    private fun makeJwt(exp: Long): String {
        val header = """{"alg":"HS256","typ":"JWT"}"""
        val payload = """{"sub":"u1","exp":$exp}"""
        val enc = Base64.getUrlEncoder().withoutPadding()
        return "${enc.encodeToString(header.toByteArray())}.${enc.encodeToString(payload.toByteArray())}.sig"
    }

    private class FakeTokenStorage(
        var access: String? = null,
        var refresh: String? = null,
    ) : TokenStorage {
        override val tokenFlow = kotlinx.coroutines.flow.flowOf(
            access?.let { TokenData(it, refresh ?: "") }
        )

        override suspend fun save(token: TokenData) {
            access = token.accessToken
            refresh = token.refreshToken
        }

        override suspend fun get(): TokenData? = access?.let { TokenData(it, refresh ?: "") }
        override suspend fun getAccessToken(): String? = access
        override suspend fun getRefreshToken(): String? = refresh
        override suspend fun clear() { access = null; refresh = null }
        override suspend fun refresh(): String? = null
    }

    private class FakeRefresher(val result: String?) : TokenRefreshProvider {
        var called = false
        override suspend fun refresh(): String? {
            called = true
            return result
        }
    }

    @Test
    fun `no access token does nothing`() = runBlocking {
        val storage = FakeTokenStorage(access = null)
        val refresher = FakeRefresher(result = "new-token")
        val controller = SessionController(storage, refresher)

        controller.ensureSession()

        assertFalse(refresher.called)
    }

    @Test
    fun `unparsable token does nothing`() = runBlocking {
        val storage = FakeTokenStorage(access = "not-a-jwt", refresh = "r")
        val refresher = FakeRefresher(result = "new-token")
        val controller = SessionController(storage, refresher)

        controller.ensureSession()

        assertFalse(refresher.called)
    }

    @Test
    fun `token expiring beyond threshold does not refresh`() = runBlocking {
        val nowSec = System.currentTimeMillis() / 1000
        val storage = FakeTokenStorage(access = makeJwt(nowSec + 300), refresh = "r")
        val refresher = FakeRefresher(result = "new-token")
        val controller = SessionController(storage, refresher)

        controller.ensureSession()

        assertFalse(refresher.called)
    }

    @Test
    fun `token near expiry refreshes successfully`() = runBlocking {
        val nowSec = System.currentTimeMillis() / 1000
        val storage = FakeTokenStorage(access = makeJwt(nowSec + 10), refresh = "r")
        val refresher = FakeRefresher(result = "new-token")
        val controller = SessionController(storage, refresher)

        controller.ensureSession()

        assertTrue(refresher.called)
    }

    @Test
    fun `token near expiry and refresh fails emits forceLogout`() = runBlocking {
        val nowSec = System.currentTimeMillis() / 1000
        val storage = FakeTokenStorage(access = makeJwt(nowSec + 10), refresh = "r")
        val refresher = FakeRefresher(result = null)
        val controller = SessionController(storage, refresher)

        // 先启动订阅（UNDISPATCHED 立即开始 collect），再触发 ensureSession
        val deferred = CompletableDeferred<Unit>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            controller.forceLogout.collect { deferred.complete(it) }
        }
        controller.ensureSession()

        assertTrue(refresher.called)
        val emitted = withTimeoutOrNull(500) { deferred.await() }
        assertEquals(Unit, emitted)
        job.cancel()
    }

    @Test
    fun `emitForceLogout is idempotent`() = runBlocking {
        val storage = FakeTokenStorage()
        val refresher = FakeRefresher(result = null)
        val controller = SessionController(storage, refresher)

        val deferred = CompletableDeferred<Unit>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            controller.forceLogout.collect { deferred.complete(it) }
        }
        controller.emitForceLogout()
        controller.emitForceLogout()

        val emitted = withTimeoutOrNull(500) { deferred.await() }
        assertEquals(Unit, emitted)
        job.cancel()
    }
}
