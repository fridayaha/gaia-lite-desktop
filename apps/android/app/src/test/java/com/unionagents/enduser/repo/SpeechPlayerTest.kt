package com.unionagents.enduser.repo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * cleanForSpeech：TTS 朗读前的 markdown 清洗。
 * 代码块/行内代码/链接语法不应被逐字读出，可见文本保留。
 */
class SpeechPlayerTest {

    @Test
    fun `fenced code block becomes placeholder`() {
        val out = SpeechPlayer.cleanForSpeech("看这段：\n```kotlin\nval a = 1\n```\n完了")
        assertFalse(out.contains("val a"))
        assertFalse(out.contains("```"))
        assertEquals("看这段：\n，代码片段，\n完了", out)
    }

    @Test
    fun `inline code backticks removed`() {
        val out = SpeechPlayer.cleanForSpeech("使用 `println` 输出")
        assertFalse(out.contains("`"))
        assertFalse(out.contains("println"))
    }

    @Test
    fun `link keeps visible text drops url`() {
        val out = SpeechPlayer.cleanForSpeech("详见 [官方文档](https://example.com/docs) 说明")
        assertEquals("详见 官方文档 说明", out)
    }

    @Test
    fun `image keeps alt text`() {
        val out = SpeechPlayer.cleanForSpeech("![架构图](https://example.com/a.png)")
        assertEquals("架构图", out)
    }

    @Test
    fun `headers bold italic strikethrough quote list markers stripped`() {
        val out = SpeechPlayer.cleanForSpeech("## 标题\n**加粗** 和 *斜体* 和 ~~删除线~~\n> 引用\n- 列表项")
        assertEquals("标题\n加粗 和 斜体 和 删除线\n引用\n列表项", out)
    }

    @Test
    fun `collapses repeated whitespace`() {
        val out = SpeechPlayer.cleanForSpeech("多  个\n\n空   格")
        assertEquals("多 个 空 格", out)
    }

    @Test
    fun `blank after cleaning returns empty`() {
        assertEquals("", SpeechPlayer.cleanForSpeech("```\ncode only\n```").replace("，代码片段，", "").trim())
    }
}
