package com.unionagents.enduser.sse

import android.os.SystemClock
import android.util.Log
import java.io.File

/**
 * 流式渲染全链路打点 —— 定位"几十秒后内容一下子全出来"类问题。
 *
 * 三层计数（每秒聚合一行，零值秒也输出——空白段正是定位线索）：
 * - sse  ：SseClient 读到 data 行（网络到达节奏）
 * - vm   ：ViewModel 处理 StreamEvent（协程/状态节奏）
 * - cmp  ：Compose 层拿到新 markdown（状态传播节奏）
 * - rndr ：MarkdownText 节流后实际渲染（UI 节奏）
 *
 * 里程碑（MARK 行）：POST 开始/拿到 run_id/SSE 开流(含响应头)/首个 delta/工具事件/流结束。
 * 只记元数据（计数、长度、耗时），不记消息内容。
 *
 * 输出双通道：logcat(TAG=StreamProbe) + filesDir/logs/stream-probe.log（设置页可复制）。
 * 文件超 [MAX_BYTES] 截断保留后半。非流式期间调用视为 no-op（begin/end 之间才计数）。
 */
object StreamProbe {

    private const val TAG = "StreamProbe"
    private const val MAX_BYTES = 512 * 1024

    /** 测试注入：虚拟时钟；默认系统单调时钟 */
    internal var clock: () -> Long = { SystemClock.elapsedRealtime() }

    /** 测试注入：logcat 输出（单测环境 android.util.Log 不可用） */
    internal var logcat: (String) -> Unit = { Log.i(TAG, it) }

    /** 测试注入：为 null 时跳过文件写入（单测不碰磁盘） */
    internal var logFile: File? = null

    /** 最近输出的行（含未 flush 的桶），单测断言用；上限 2000 行防内存膨胀 */
    internal val recentLines = ArrayDeque<String>()

    private val lock = Any()
    private var t0 = 0L
    private var active = false

    // 当前秒桶的计数器
    private var bucketSec = -1L
    private var sseCount = 0
    private var sseBytes = 0
    private var vmCount = 0
    private var cmpCount = 0
    private var rndrCount = 0
    private var rndrBytes = 0

    // 累计
    private var totalSse = 0
    private var totalVm = 0
    private var totalCmp = 0
    private var totalRndr = 0

    fun init(filesDir: File) {
        logFile = File(filesDir, "logs/stream-probe.log").apply { parentFile?.mkdirs() }
    }

    /** App 启动头（版本/设备），无条件写入一行分隔 */
    fun appStart(header: String) {
        synchronized(lock) { line("──── APP $header") }
    }

    /** 一次流式会话开始（sendMessage 发出时）。meta: agent/model/engine 等标识。 */
    fun begin(meta: String) {
        synchronized(lock) {
            t0 = clock()
            active = true
            bucketSec = -1L
            sseCount = 0; sseBytes = 0; vmCount = 0; cmpCount = 0; rndrCount = 0; rndrBytes = 0
            totalSse = 0; totalVm = 0; totalCmp = 0; totalRndr = 0
            line("════ BEGIN $meta")
        }
    }

    /** 里程碑：瞬时事件，立即输出一行 */
    fun mark(phase: String, detail: String = "") {
        synchronized(lock) {
            if (!active) return
            flushBucketsUpTo(elapsedSec())
            line("t=+${elapsedMs()}ms MARK $phase${if (detail.isEmpty()) "" else " $detail"}")
        }
    }

    /** SSE 层：收到一条 data payload */
    fun tickSse(payloadChars: Int) = tick(sse = 1, sseB = payloadChars)

    /** ViewModel 层：处理一个 StreamEvent */
    fun tickVm() = tick(vm = 1)

    /** Compose 层：streamingContent 变化传播到 MarkdownText（未节流） */
    fun tickCompose() = tick(cmp = 1)

    /** UI 层：一次实际 markdown 渲染 */
    fun tickRender(textChars: Int) = tick(rndr = 1, rndrB = textChars)

    private fun tick(sse: Int = 0, sseB: Int = 0, vm: Int = 0, cmp: Int = 0, rndr: Int = 0, rndrB: Int = 0) {
        synchronized(lock) {
            if (!active) return
            val sec = elapsedSec()
            flushBucketsUpTo(sec)
            if (bucketSec < sec) bucketSec = sec
            sseCount += sse; sseBytes += sseB
            vmCount += vm
            cmpCount += cmp
            rndrCount += rndr; rndrBytes += rndrB
            totalSse += sse; totalVm += vm; totalCmp += cmp; totalRndr += rndr
        }
    }

    /** 流结束（completed/failed/eof）。reason 说明收尾方式。 */
    fun end(reason: String) {
        synchronized(lock) {
            if (!active) return
            flushBucketsUpTo(elapsedSec())
            flushBucket()
            line("════ END $reason dur=${elapsedMs()}ms sse=$totalSse vm=$totalVm cmp=$totalCmp rndr=$totalRndr")
            active = false
        }
    }

    // ── 内部 ──

    private fun elapsedMs() = clock() - t0
    private fun elapsedSec() = elapsedMs() / 1000

    /** 把 [bucketSec, sec) 之间未输出的秒桶逐秒输出（含零值桶），最后把 bucketSec 对齐到 sec-1 */
    private fun flushBucketsUpTo(sec: Long) {
        if (bucketSec < 0) {
            bucketSec = sec - 1
            if (bucketSec < 0) bucketSec = 0
        }
        while (bucketSec < sec) {
            flushBucket()
            bucketSec++
        }
    }

    private fun flushBucket() {
        if (bucketSec < 0) return
        line("t=+${bucketSec}s sse=$sseCount/${sseBytes}B vm=$vmCount cmp=$cmpCount rndr=$rndrCount/${rndrBytes}B")
        sseCount = 0; sseBytes = 0; vmCount = 0; cmpCount = 0; rndrCount = 0; rndrBytes = 0
    }

    private fun line(s: String) {
        logcat(s)
        synchronized(recentLines) {
            recentLines.addLast(s)
            while (recentLines.size > 2000) recentLines.removeFirst()
        }
        val f = logFile ?: return
        runCatching {
            if (f.length() > MAX_BYTES) {
                val tail = f.readBytes().copyOfRange(MAX_BYTES / 2, f.length().toInt())
                f.writeBytes(tail)
            }
            f.appendText(s + "\n")
        }
    }

    /** 设置页读取展示 */
    fun readLog(): String = runCatching { logFile?.takeIf { it.exists() }?.readText() ?: "" }.getOrDefault("")

    fun clearLog() {
        runCatching { logFile?.delete() }
        synchronized(recentLines) { recentLines.clear() }
    }
}
