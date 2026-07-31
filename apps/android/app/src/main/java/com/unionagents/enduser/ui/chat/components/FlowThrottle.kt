package com.unionagents.enduser.ui.chat.components

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.collectLatest

/**
 * 最新值节流（leading + trailing）：窗口内的首个值立即发出，随后窗口期内到达的值只保留
 * 最新一个，在窗口结束时发出。
 *
 * 用途：流式渲染场景（如 SSE delta 高峰可达 ~76/s）下游若挂重渲染（Markwon 全量解析
 * 长文本 + Linkify 正则），逐值渲染会把主线程打满（总开销 O(n²)），表现为 UI 冻结、
 * 流结束后内容"一下子全出来"。节流后渲染帧率上限 1000/periodMs。
 *
 * 实现用 collectLatest：窗口内新值到达会取消未完成的延迟，保证最终一定发出最新值。
 * 注意必须 channelFlow：collectLatest 的发射发生在其子协程里，普通 flow{} 的 emit
 * 会触发 "Flow invariant is violated"。
 *
 * @param periodMs 节流窗口（毫秒）
 * @param clock 时钟（测试时注入虚拟时钟）
 */
fun <T> Flow<T>.throttleLatest(
    periodMs: Long,
    clock: () -> Long = System::currentTimeMillis,
): Flow<T> = channelFlow {
    // 首值必须立即发出。哨兵不能用 Long.MIN_VALUE：真实时钟 ~1.7e12，c - MIN_VALUE 溢出
    // 为负，wait = period - 负值 ≈ +9.2e18ms → delay 到永远，leading 永不发射（生产冻结）。
    var lastEmitAt: Long? = null
    collectLatest { value ->
        val wait = lastEmitAt?.let { periodMs - (clock() - it) } ?: 0L
        if (wait > 0) delay(wait)
        send(value)
        lastEmitAt = clock()
    }
}

/**
 * 流式 markdown 渲染前把未闭合的代码围栏临时补上 ``` 闭合，避免 Markwon 把后续文本
 * 全部吞进代码块。反引号数 /3 计围栏数，奇数说明有未闭合围栏。
 */
internal fun closeUnclosedFences(markdown: String): String {
    val fenceCount = markdown.count { it == '`' } / 3
    return if (fenceCount % 2 == 1) "$markdown\n```" else markdown
}
