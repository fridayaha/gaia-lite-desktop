package com.unionagents.enduser.ui.chat.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolCardTruncateTest {

    // ── truncatePreview ───────────────────────────────────────

    @Test
    fun `truncatePreview under 120 chars returns as-is`() {
        val p = "a".repeat(100)
        assertEquals(p, truncatePreview(p))
    }

    @Test
    fun `truncatePreview exactly 120 chars returns as-is`() {
        val p = "a".repeat(120)
        assertEquals(p, truncatePreview(p))
    }

    @Test
    fun `truncatePreview over 120 cuts at last space when present`() {
        // 130 chars, 中间有空格 → 在空格处截断
        val p = "a".repeat(60) + " " + "b".repeat(69)
        val out = truncatePreview(p)
        assertTrue("应截断到 < 121", out.length <= 121)
        assertTrue(out.endsWith("…"))
        assertTrue(out.startsWith("a".repeat(60)))
    }

    @Test
    fun `truncatePreview over 120 without space cuts at 120`() {
        val p = "x".repeat(200)
        val out = truncatePreview(p)
        assertEquals("x".repeat(120) + "…", out)
    }

    @Test
    fun `truncatePreview break char below threshold ignored`() {
        // lastBreak 必须大于 40 才用；否则原 cut
        val p = "a".repeat(50) + " " + "b".repeat(80)
        val out = truncatePreview(p)
        // 空格位置 50 > 40 → 在 50 处截断
        assertEquals("a".repeat(50) + "…", out)
    }

    // ── truncateResult ────────────────────────────────────────

    @Test
    fun `truncateResult under 800 chars returns as-is`() {
        val r = "a".repeat(500)
        assertEquals(r, truncateResult(r))
    }

    @Test
    fun `truncateResult exactly 800 returns as-is`() {
        val r = "a".repeat(800)
        assertEquals(r, truncateResult(r))
    }

    @Test
    fun `truncateResult over 800 cuts at newline when present`() {
        val r = "a".repeat(500) + "\n" + "b".repeat(400)
        val out = truncateResult(r)
        assertTrue(out.endsWith("…"))
        // 在 \n 处截断（位置 500 > 400）
        assertEquals("a".repeat(500) + "…", out)
    }

    @Test
    fun `truncateResult over 800 cuts at sentence end when no newline`() {
        val r = "a".repeat(500) + ". " + "b".repeat(400)
        val out = truncateResult(r)
        assertTrue(out.endsWith("…"))
        // 在 ". " 起始位置 500 截断，take(500) 不含 . 与空格
        assertEquals("a".repeat(500) + "…", out)
    }

    @Test
    fun `truncateResult over 800 without any break cuts at 800`() {
        val r = "x".repeat(1500)
        val out = truncateResult(r)
        assertEquals("x".repeat(800) + "…", out)
    }

    @Test
    fun `truncateResult break position below 400 ignored`() {
        // lastBreak 必须大于 400 才用；否则原 cut 800
        val r = "a".repeat(300) + "\n" + "b".repeat(600)
        val out = truncateResult(r)
        // \n 位置 300 < 400 → 不用 break，直接 cut 800（300a + \n + 499b）+ …
        assertEquals("a".repeat(300) + "\n" + "b".repeat(499) + "…", out)
    }

    @Test
    fun `truncateResult handles Chinese full stop as break`() {
        val r = "a".repeat(500) + "。" + "b".repeat(400)
        val out = truncateResult(r)
        assertTrue(out.endsWith("…"))
        // 在 。 位置 500 截断，take(500) 不含 。
        assertEquals("a".repeat(500) + "…", out)
    }
}
