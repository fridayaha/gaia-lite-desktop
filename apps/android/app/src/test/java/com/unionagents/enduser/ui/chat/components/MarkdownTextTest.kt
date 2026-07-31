package com.unionagents.enduser.ui.chat.components

import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTextTest {

    // ── splitBlocks ───────────────────────────────────────────

    @Test
    fun `splitBlocks empty input yields empty list`() {
        assertTrue(splitBlocks("").isEmpty())
    }

    @Test
    fun `splitBlocks single paragraph yields one paragraph block`() {
        val blocks = splitBlocks("hello world")
        assertEquals(1, blocks.size)
        assertEquals(BlockType.PARAGRAPH, blocks[0].type)
        assertEquals("hello world", blocks[0].text)
    }

    @Test
    fun `splitBlocks blank line separates paragraphs`() {
        val blocks = splitBlocks("第一段\n\n第二段")
        assertEquals(2, blocks.size)
        assertEquals(BlockType.PARAGRAPH, blocks[0].type)
        assertEquals(BlockType.PARAGRAPH, blocks[1].type)
        assertEquals("第一段", blocks[0].text)
        assertEquals("第二段", blocks[1].text)
    }

    @Test
    fun `splitBlocks closed code fence yields one code_fence block`() {
        val md = "```kotlin\nval x = 1\n```"
        val blocks = splitBlocks(md)
        assertEquals(1, blocks.size)
        assertEquals(BlockType.CODE_FENCE, blocks[0].type)
        // 围栏内容应整体保留
        assertEquals(md, blocks[0].text)
    }

    @Test
    fun `splitBlocks unclosed code fence at EOF still becomes code_fence block`() {
        // closeUnclosedFences 已经补过 ``` 后，splitBlocks 应识别为 CODE_FENCE
        val md = "```python\nprint('hi')"
        val blocks = splitBlocks(md)
        assertEquals(1, blocks.size)
        assertEquals(BlockType.CODE_FENCE, blocks[0].type)
    }

    @Test
    fun `splitBlocks paragraph then code fence then paragraph`() {
        val md = "前文\n```kotlin\ncode\n```\n后文"
        val blocks = splitBlocks(md)
        assertEquals(3, blocks.size)
        assertEquals(BlockType.PARAGRAPH, blocks[0].type)
        assertEquals(BlockType.CODE_FENCE, blocks[1].type)
        assertEquals(BlockType.PARAGRAPH, blocks[2].type)
        assertEquals("前文", blocks[0].text)
        assertEquals("后文", blocks[2].text)
    }

    @Test
    fun `splitBlocks tilde fence marker also recognized`() {
        val md = "~~~\ncode\n~~~"
        val blocks = splitBlocks(md)
        assertEquals(1, blocks.size)
        assertEquals(BlockType.CODE_FENCE, blocks[0].type)
    }

    @Test
    fun `splitBlocks blank line inside code fence does not split fence`() {
        val md = "```\nline1\n\nline2\n```"
        val blocks = splitBlocks(md)
        assertEquals(1, blocks.size)
        assertEquals(BlockType.CODE_FENCE, blocks[0].type)
        // 围栏内的空行必须保留在同一个 block 内
        assertTrue(blocks[0].text.contains("line1\n\nline2"))
    }

    // ── parseInlineParagraph ──────────────────────────────────

    @Test
    fun `parseInlineParagraph plain text has no styles and no annotations`() {
        val ann = parseInlineParagraph("just text")
        assertEquals("just text", ann.text)
        assertTrue(ann.spanStyles.isEmpty())
        assertTrue(ann.getLinkAnnotations(0, ann.length).isEmpty())
    }

    @Test
    fun `parseInlineParagraph double star wraps bold`() {
        val ann = parseInlineParagraph("a **bold** b")
        assertEquals("a bold b", ann.text)
        val bold = ann.spanStyles.single()
        assertEquals("bold", ann.text.substring(bold.start, bold.end))
        assertEquals(FontWeight.Bold, bold.item.fontWeight)
    }

    @Test
    fun `parseInlineParagraph double underscore wraps bold`() {
        val ann = parseInlineParagraph("a __bold__ b")
        assertEquals("a bold b", ann.text)
        val bold = ann.spanStyles.single()
        assertEquals("bold", ann.text.substring(bold.start, bold.end))
        assertEquals(FontWeight.Bold, bold.item.fontWeight)
    }

    @Test
    fun `parseInlineParagraph single star wraps italic`() {
        val ann = parseInlineParagraph("a *italic* b")
        assertEquals("a italic b", ann.text)
        val italic = ann.spanStyles.single()
        assertEquals("italic", ann.text.substring(italic.start, italic.end))
        assertEquals(FontStyle.Italic, italic.item.fontStyle)
    }

    @Test
    fun `parseInlineParagraph single underscore wraps italic`() {
        val ann = parseInlineParagraph("a _italic_ b")
        assertEquals("a italic b", ann.text)
        val italic = ann.spanStyles.single()
        assertEquals("italic", ann.text.substring(italic.start, italic.end))
        assertEquals(FontStyle.Italic, italic.item.fontStyle)
    }

    @Test
    fun `parseInlineParagraph backtick wraps monospace code`() {
        val ann = parseInlineParagraph("run `ls -al` now")
        assertEquals("run ls -al now", ann.text)
        val code = ann.spanStyles.single()
        assertEquals("ls -al", ann.text.substring(code.start, code.end))
        assertEquals(FontFamily.Monospace, code.item.fontFamily)
    }

    @Test
    fun `parseInlineParagraph link renders label and stores url annotation`() {
        val ann = parseInlineParagraph("see [docs](https://example.com/x) now")
        assertEquals("see docs now", ann.text)
        val link = ann.getLinkAnnotations(0, ann.length).single()
        assertEquals("docs", ann.text.substring(link.start, link.end))
        assertTrue(link.item is LinkAnnotation.Url)
        assertEquals("https://example.com/x", (link.item as LinkAnnotation.Url).url)
        val styles = (link.item as LinkAnnotation.Url).styles!!
        val normal = styles.style!!
        assertEquals(TextDecoration.Underline, normal.textDecoration)
    }

    @Test
    fun `parseInlineParagraph unmatched markers left as plain text`() {
        // 单个 ` 或 [ 无法成对，不应产生任何样式
        val ann = parseInlineParagraph("a ` b [ c")
        assertEquals("a ` b [ c", ann.text)
        assertTrue(ann.spanStyles.isEmpty())
        assertTrue(ann.getLinkAnnotations(0, ann.length).isEmpty())
    }

    @Test
    fun `parseInlineParagraph multiple styles in one paragraph`() {
        val ann = parseInlineParagraph("**bold** and *italic* and `code`")
        assertEquals("bold and italic and code", ann.text)
        assertEquals(3, ann.spanStyles.size)
    }

    @Test
    fun `parseInlineParagraph link without closing paren stays plain`() {
        // 链接未闭合右括号：不识别为 link，直接当作普通 [text](url 字符
        val ann = parseInlineParagraph("broken [docs](https://example.com end")
        assertTrue(ann.getLinkAnnotations(0, ann.length).isEmpty())
    }
}
