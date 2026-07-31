package com.unionagents.enduser.sse

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * StreamProbe 打点聚合验证：
 * - 秒桶聚合：同秒的 tick 合并为一行，跨秒自动 flush
 * - 零值秒也输出（空白段是"几十秒没动静"的直接证据）
 * - begin/end 生命周期外 tick/mark 是 no-op
 * - 只记计数与字节数，不记内容
 */
class StreamProbeTest {

    private var now = 0L

    @Before
    fun setUp() {
        now = 0L
        StreamProbe.clock = { now }
        StreamProbe.logcat = {}
        StreamProbe.logFile = null
        synchronized(StreamProbe.recentLines) { StreamProbe.recentLines.clear() }
    }

    @After
    fun tearDown() {
        // 防止用例间状态泄漏
        StreamProbe.end("teardown")
        synchronized(StreamProbe.recentLines) { StreamProbe.recentLines.clear() }
    }

    private fun lines(): List<String> = synchronized(StreamProbe.recentLines) {
        StreamProbe.recentLines.toList()
    }

    @Test
    fun `ticks within same second aggregate into one bucket line`() {
        StreamProbe.begin("test")
        repeat(5) { StreamProbe.tickSse(10) }
        repeat(3) { StreamProbe.tickVm() }
        now = 1500 // 跨秒触发 flush
        StreamProbe.tickSse(1)
        StreamProbe.end("done")

        val bucket0 = lines().first { it.startsWith("t=+0s") }
        assertEquals("t=+0s sse=5/50B vm=3 cmp=0 rndr=0/0B", bucket0)
    }

    @Test
    fun `idle seconds emit zero buckets so gaps are visible`() {
        StreamProbe.begin("test")
        StreamProbe.tickSse(10)
        now = 3000 // t=3s 才来下一个事件，中间 t=1s t=2s 必须是零值桶
        StreamProbe.tickSse(10)
        StreamProbe.end("done")

        val buckets = lines().filter { it.startsWith("t=+") && !it.contains("MARK") }
        assertEquals("t=+0s sse=1/10B vm=0 cmp=0 rndr=0/0B", buckets[0])
        assertEquals("t=+1s sse=0/0B vm=0 cmp=0 rndr=0/0B", buckets[1])
        assertEquals("t=+2s sse=0/0B vm=0 cmp=0 rndr=0/0B", buckets[2])
        assertEquals("t=+3s sse=1/10B vm=0 cmp=0 rndr=0/0B", buckets[3])
    }

    @Test
    fun `mark flushes pending buckets and prints milestone with elapsed ms`() {
        StreamProbe.begin("test")
        StreamProbe.tickSse(10)
        now = 2340
        StreamProbe.mark("FIRST_DELTA")

        val out = lines()
        assertTrue(out.any { it.startsWith("t=+0s") })
        assertTrue(out.any { it == "t=+2340ms MARK FIRST_DELTA" })
        StreamProbe.end("done")
    }

    @Test
    fun `end prints summary totals and stops counting`() {
        StreamProbe.begin("test")
        repeat(4) { StreamProbe.tickSse(5) }
        repeat(4) { StreamProbe.tickVm() }
        repeat(2) { StreamProbe.tickCompose() }
        StreamProbe.tickRender(100)
        now = 12000
        StreamProbe.end("completed")

        val endLine = lines().last { it.startsWith("════ END") }
        assertEquals("════ END completed dur=12000ms sse=4 vm=4 cmp=2 rndr=1", endLine)

        // end 之后打点失效
        val sizeAfterEnd = lines().size
        StreamProbe.tickSse(10)
        StreamProbe.mark("X")
        assertEquals(sizeAfterEnd, lines().size)
    }

    @Test
    fun `ticks before begin are ignored`() {
        StreamProbe.tickSse(10)
        StreamProbe.mark("X")
        assertTrue(lines().isEmpty())
    }

    @Test
    fun `render and compose counters tracked separately`() {
        StreamProbe.begin("test")
        repeat(76) { StreamProbe.tickCompose() }  // Compose 逐 token 收到
        repeat(9) { StreamProbe.tickRender(500) } // 节流后只渲染 9 次
        now = 1000
        StreamProbe.tickVm() // 触发 flush
        StreamProbe.end("done")

        val bucket0 = lines().first { it.startsWith("t=+0s") }
        assertEquals("t=+0s sse=0/0B vm=0 cmp=76 rndr=9/4500B", bucket0)
    }
}
