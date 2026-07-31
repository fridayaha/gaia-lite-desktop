package com.unionagents.enduser.sse

import com.unionagents.enduser.di.buildSseOkHttpClient
import com.unionagents.enduser.net.SessionController
import com.unionagents.enduser.net.TokenData
import com.unionagents.enduser.net.TokenRefresher
import com.unionagents.enduser.net.TokenStorage
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.logging.HttpLoggingInterceptor
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import kotlin.concurrent.thread

/**
 * SSE 到达节奏回归 —— "几十秒后内容一下全出来"的根因防护。
 *
 * 根因：NetworkModule 给 GatewayClient 挂了 HttpLoggingInterceptor(Level.BODY)，其实现
 * `source.request(Long.MAX_VALUE)` 把响应体缓冲到 EOF 才返回 response——SSE 流被扣留到
 * run 结束一次性放行。SseClient 构造时剥离该拦截器（newBuilder 共享连接池/鉴权/超时）。
 *
 * 测试服务器逐 chunk 滴流（首 chunk → 停 2s → 末 chunk）：流式正常时首事件远早于 EOF 到达；
 * 被缓冲时首事件 ≥2s。MockWebServer 对单 body 的分块 flush 时机不可控，故用裸 ServerSocket。
 */
class SseClientStreamTest {

    private lateinit var serverSocket: ServerSocket
    private val dripDelayMs = 2000L

    @Before
    fun setUp() {
        serverSocket = ServerSocket(0)
        thread(isDaemon = true) {
            while (!serverSocket.isClosed) {
                val socket = runCatching { serverSocket.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { runCatching { serveSseDrip(socket) } }
            }
        }
    }

    @After
    fun tearDown() {
        runCatching { serverSocket.close() }
    }

    /** 读请求头 → 发 chunked SSE：data: first 立即 flush，停 2s，再 data: second + 终止 chunk。 */
    private fun serveSseDrip(socket: Socket) {
        socket.use { s ->
            val reader = s.getInputStream().bufferedReader()
            while (reader.readLine()?.isNotEmpty() == true) Unit
            val out = s.getOutputStream()
            out.write(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                    .toByteArray(),
            )
            out.flush()
            writeChunk(out, "data: first\n\n")
            Thread.sleep(dripDelayMs)
            writeChunk(out, "data: second\n\n")
            writeChunk(out, "")
        }
    }

    private fun writeChunk(out: OutputStream, data: String) {
        val bytes = data.toByteArray(Charsets.UTF_8)
        out.write("${bytes.size.toString(16)}\r\n".toByteArray())
        out.write(bytes)
        out.write("\r\n".toByteArray())
        out.flush()
    }

    private fun request(): Request =
        Request.Builder().url("http://127.0.0.1:${serverSocket.localPort}/v1/runs/r1/events").build()

    private fun fakeStorage() = object : TokenStorage {
        override val tokenFlow: Flow<TokenData?> = MutableStateFlow(null)
        override suspend fun save(token: TokenData) {}
        override suspend fun get(): TokenData? = null
        override suspend fun getAccessToken(): String? = null
        override suspend fun getRefreshToken(): String? = null
        override suspend fun clear() {}
        override suspend fun refresh(): String? = null
    }

    private val passthrough = Interceptor { chain -> chain.proceed(chain.request()) }

    private fun sseOkHttpClient(): OkHttpClient =
        buildSseOkHttpClient(passthrough, passthrough, Authenticator.NONE)

    private fun sseClient(base: OkHttpClient): SseClient {
        val storage = fakeStorage()
        val refresher = TokenRefresher(storage, Json { ignoreUnknownKeys = true }, OkHttpClient(), "http://127.0.0.1/")
        return SseClient(base, refresher, SessionController(storage, refresher))
    }

    @Test
    fun `buildSseOkHttpClient never carries a body-buffering logging interceptor`() {
        assertFalse(
            "SSE client must not include HttpLoggingInterceptor",
            sseOkHttpClient().interceptors.any { it is HttpLoggingInterceptor },
        )
    }

    @Test
    fun `BODY logging interceptor buffers whole body until eof - root cause mechanism`() = runBlocking {
        val loggingClient = OkHttpClient.Builder()
            .addInterceptor(HttpLoggingInterceptor {}.apply { level = HttpLoggingInterceptor.Level.BODY })
            .build()

        val plainMs = withContext(Dispatchers.IO) {
            val t0 = System.nanoTime()
            OkHttpClient().newCall(request()).execute().close()
            (System.nanoTime() - t0) / 1_000_000
        }
        val loggingMs = withContext(Dispatchers.IO) {
            val t0 = System.nanoTime()
            loggingClient.newCall(request()).execute().close()
            (System.nanoTime() - t0) / 1_000_000
        }

        assertTrue("plain client gets headers immediately, was ${plainMs}ms", plainMs < dripDelayMs / 2)
        assertTrue(
            "BODY logging blocks execute() until EOF (~${dripDelayMs}ms), was ${loggingMs}ms",
            loggingMs >= dripDelayMs * 3 / 4,
        )
    }

    @Test
    fun `SseClient over DI-built SSE client streams first event before eof`() = runBlocking {
        val events = mutableListOf<Pair<String, Long>>()
        val t0 = System.nanoTime()

        withTimeout(10_000) {
            sseClient(sseOkHttpClient()).stream(request()).collect {
                events += it to (System.nanoTime() - t0) / 1_000_000
            }
        }

        assertEquals(listOf("first", "second"), events.map { it.first })
        assertTrue(
            "first event must stream long before EOF, arrived at ${events[0].second}ms",
            events[0].second < dripDelayMs / 2,
        )
        assertTrue(
            "second event arrives after server drip, was ${events[1].second}ms",
            events[1].second >= dripDelayMs * 3 / 4,
        )
    }
}
