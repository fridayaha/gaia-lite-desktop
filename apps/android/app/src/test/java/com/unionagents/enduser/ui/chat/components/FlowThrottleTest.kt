package com.unionagents.enduser.ui.chat.components

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 验证流式渲染的两个工具：
 * - [throttleLatest]：leading+trailing 节流，窗口内只发最新值（防止逐 token 重渲染打满主线程）
 * - [closeUnclosedFences]：未闭合代码围栏补 ``` 闭合
 *
 * 测试时钟统一用 runTest 的虚拟时间 [currentTime]，上游用 delay() 推进时间轴——
 * 手写 now 变量不会触发调度器推进，挂起的 trailing 延迟会被后续值直接取消，测不出真实时序。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class FlowThrottleTest {

    // ── throttleLatest ─────────────────────────────────────────

    @Test
    fun `first value emits immediately without waiting`() = runTest {
        val result = flow {
            emit("a")
        }.throttleLatest(80, clock = { currentTime }).toList()
        assertEquals(listOf("a"), result)
    }

    @Test
    fun `values spaced beyond window all pass through`() = runTest {
        val result = flow {
            emit("a"); delay(100)
            emit("b"); delay(100)
            emit("c")
        }.throttleLatest(80, clock = { currentTime }).toList()
        assertEquals(listOf("a", "b", "c"), result)
    }

    @Test
    fun `burst within window keeps only latest`() = runTest {
        val result = flow {
            emit("a")          // t=0 leading：立即发
            delay(10)
            emit("b")          // t=10 窗口内：被 c 取代
            delay(10)
            emit("c")          // t=20 窗口内最新：t=80 trailing 发出
            delay(200)
            emit("d")          // t=220 越过窗口：立即发
        }.throttleLatest(80, clock = { currentTime }).toList()
        assertEquals(listOf("a", "c", "d"), result)
    }

    @Test
    fun `continuous stream collapses to window-rate updates`() = runTest {
        // 模拟 SSE delta 风暴：100 个 token 每 5ms 一个（虚拟时间），80ms 窗口
        val emitted = flow {
            repeat(100) { i ->
                emit("t$i")
                delay(5)
            }
        }.throttleLatest(80, clock = { currentTime }).toList()
        // leading 1 个 + 500ms 内每 80ms 一个 trailing ≈ 8 个，远少于 100
        assertEquals("t0", emitted.first())
        assertEquals("t99", emitted.last()) // 最后一个值必须发出（不丢尾巴）
        assert(emitted.size <= 10) { "expected collapse, got ${emitted.size}: $emitted" }
    }

    @Test
    fun `empty flow emits nothing`() = runTest {
        val result = flow<String> { }
            .throttleLatest(80, clock = { currentTime })
            .toList()
        assertEquals(emptyList<String>(), result)
    }

    @Test
    fun `slow producer delay still bounded by collectLatest cancellation`() = runTest {
        // trailing 延迟期间来了更新值：旧值被取消，发新值
        val result = flow {
            emit("a")           // t=0 leading
            delay(10)
            emit("b")           // t=10 安排 trailing（等 70ms）
            delay(30)
            emit("c")           // t=40 取代 b，重新等窗口（40ms 后 t=80 发出）
            delay(200)
        }.throttleLatest(80, clock = { currentTime }).toList()
        assertEquals(listOf("a", "c"), result)
    }

    @Test
    fun `leading emission is immediate even with real-scale clock values`() = runTest {
        // 回归：lastEmit 曾用 Long.MIN_VALUE 哨兵，真实时钟（~1.7e12）下 c - MIN_VALUE 溢出
        // 为负，wait ≈ +9.2e18ms → leading delay 到永远（生产冻结、rndr 恒 0）。
        // 用虚拟时间断言：若 leading 被延迟，currentTime 会跳到天文数字。
        val receivedAt = mutableListOf<Long>()
        flow { emit("a") }
            .throttleLatest(80, clock = { 1_750_000_000_000L })
            .collect { receivedAt += currentTime }
        assertEquals(listOf(0L), receivedAt)
    }

    // ── closeUnclosedFences ────────────────────────────────────

    @Test
    fun `closed fence left untouched`() {
        val md = "前文 ```kotlin\ncode\n``` 后文"
        assertEquals(md, closeUnclosedFences(md))
    }

    @Test
    fun `unclosed fence gets closed`() {
        assertEquals("前文 ```\ncode\n```", closeUnclosedFences("前文 ```\ncode"))
    }

    @Test
    fun `inline code backticks do not trigger close`() {
        val md = "执行 `ls` 查看"
        assertEquals(md, closeUnclosedFences(md))
    }

    @Test
    fun `plain text untouched`() {
        assertEquals("普通文本", closeUnclosedFences("普通文本"))
    }
}
