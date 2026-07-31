package com.unionagents.enduser.ui.chat.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal enum class BlockType { PARAGRAPH, CODE_FENCE }

internal data class MdBlock(val type: BlockType, val text: String)

/**
 * 把 markdown 按「段落 / 代码块」切分（代码围栏感知）：
 * - ``` 围栏内的内容整段归 CODE_FENCE，不按空行切分
 * - 围栏外：连续非空行 = 段落；空行 = 段落边界
 *
 * 用于增量渲染：完成段落的解析结果按 block.text 缓存（remember(block.text)），
 * 仅活动尾段（每次仍在追加的最后一个 block）需要重新解析——把 O(n²) 的全量
 * Markwon 解析降为 O(段落大小) per delta。
 */
internal fun splitBlocks(markdown: String): List<MdBlock> {
    if (markdown.isEmpty()) return emptyList()
    val blocks = mutableListOf<MdBlock>()
    val current = StringBuilder()
    var inFence = false
    val fenceMarker = StringBuilder()

    fun flushParagraph() {
        if (current.isNotEmpty()) {
            blocks.add(MdBlock(BlockType.PARAGRAPH, current.toString()))
            current.setLength(0)
        }
    }

    markdown.split('\n').forEach { line ->
        val trimmed = line.trim()
        val fenceMatch = trimmed.startsWith("```") || trimmed.startsWith("~~~")
        if (fenceMatch && !inFence) {
            flushParagraph()
            inFence = true
            fenceMarker.setLength(0)
            fenceMarker.append(trimmed.take(3))
            current.append(line)
        } else if (inFence && trimmed.startsWith(fenceMarker.toString())) {
            if (current.isNotEmpty()) current.append('\n')
            current.append(line)
            blocks.add(MdBlock(BlockType.CODE_FENCE, current.toString()))
            current.setLength(0)
            inFence = false
        } else if (!inFence && line.isBlank()) {
            flushParagraph()
        } else {
            if (current.isNotEmpty()) current.append('\n')
            current.append(line)
        }
    }
    // EOF 时仍有未闭合围栏：归为 CODE_FENCE（closeUnclosedFences 已补过）
    if (inFence) {
        blocks.add(MdBlock(BlockType.CODE_FENCE, current.toString()))
    } else {
        flushParagraph()
    }
    return blocks
}

/**
 * 段落内联 markdown → AnnotatedString：支持 **bold** / *italic* / `code` / [text](url)。
 * 嵌套不处理（如 **bold *italic*** 不拆开），覆盖绝大多数聊天回复场景。
 */
internal fun parseInlineParagraph(
    text: String,
    textColor: Color = Color.Unspecified,
    linkColor: Color = Color.Unspecified,
): AnnotatedString = buildAnnotatedString {
    var i = 0
    val n = text.length
    fun appendPlain(start: Int, end: Int) {
        if (start < end) append(text, start, end)
    }
    while (i < n) {
        val c = text[i]
        when {
            c == '*' && i + 1 < n && text[i + 1] == '*' -> {
                val end = text.indexOf("**", i + 2)
                if (end > i + 2) {
                    pushStyle(SpanStyle(fontWeight = FontWeight.Bold, color = textColor))
                    append(text, i + 2, end)
                    pop()
                    i = end + 2
                } else {
                    append(c.toString()); i++
                }
            }
            c == '_' && i + 1 < n && text[i + 1] == '_' -> {
                val end = text.indexOf("__", i + 2)
                if (end > i + 2) {
                    pushStyle(SpanStyle(fontWeight = FontWeight.Bold, color = textColor))
                    append(text, i + 2, end)
                    pop()
                    i = end + 2
                } else {
                    append(c.toString()); i++
                }
            }
            c == '*' -> {
                val end = text.indexOf('*', i + 1)
                if (end > i + 1) {
                    pushStyle(SpanStyle(fontStyle = FontStyle.Italic, color = textColor))
                    append(text, i + 1, end)
                    pop()
                    i = end + 1
                } else {
                    append(c.toString()); i++
                }
            }
            c == '_' -> {
                val end = text.indexOf('_', i + 1)
                if (end > i + 1) {
                    pushStyle(SpanStyle(fontStyle = FontStyle.Italic, color = textColor))
                    append(text, i + 1, end)
                    pop()
                    i = end + 1
                } else {
                    append(c.toString()); i++
                }
            }
            c == '`' -> {
                val end = text.indexOf('`', i + 1)
                if (end > i + 1) {
                    pushStyle(SpanStyle(
                        fontFamily = FontFamily.Monospace,
                        color = textColor,
                    ))
                    append(text, i + 1, end)
                    pop()
                    i = end + 1
                } else {
                    append(c.toString()); i++
                }
            }
            c == '[' -> {
                val close = text.indexOf(']', i + 1)
                val urlEnd = if (close > i + 1 && close + 1 < n && text[close + 1] == '(') {
                    text.indexOf(')', close + 2)
                } else -1
                if (urlEnd > close + 2) {
                    val label = text.substring(i + 1, close)
                    val url = text.substring(close + 2, urlEnd)
                    pushLink(
                        LinkAnnotation.Url(
                            url = url,
                            styles = TextLinkStyles(
                                style = SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline),
                            ),
                        ),
                    )
                    append(label)
                    pop()
                    i = urlEnd + 1
                } else {
                    append(c.toString()); i++
                }
            }
            else -> {
                val next = i + 1
                val stop = (next until n).firstOrNull { text[it] == '*' || text[it] == '_' || text[it] == '`' || text[it] == '[' } ?: n
                appendPlain(i, stop)
                i = stop
            }
        }
    }
}

/**
 * 代码块渲染：表面 variant 背景 + 等宽字体 + 横向滚动由内容自适应。
 * 第一行（围栏行）解析语言标签但不展示围栏符号；最后一行（闭合围栏）剔除。
 */
@Composable
private fun CodeBlockSurface(text: String, baseTextColor: Color, modifier: Modifier = Modifier) {
    val lines = text.split('\n').filter { it.isNotEmpty() }
    val content = lines.drop(1).dropLast(1).joinToString("\n")
    val firstLine = lines.firstOrNull()?.trim() ?: ""
    val lang = firstLine.removePrefix("```").removePrefix("~~~").trim()

    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(6.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
            if (lang.isNotEmpty()) {
                Text(
                    text = lang,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                )
                Spacer(Modifier.height(4.dp))
            }
            Text(
                text = content,
                style = MaterialTheme.typography.bodySmall.copy(
                    fontFamily = FontFamily.Monospace,
                    color = baseTextColor,
                ),
            )
        }
    }
}

/**
 * Compose 原生 Markdown 渲染（替代 Markwon + AndroidView）：
 * - 段落 / 代码围栏感知 block 切分
 * - 每段 AnnotatedString 按 block.text 缓存（remember(block.text)），仅活动尾段重解析
 * - 内联样式：**bold** / *italic* / `code` / [text](url)
 * - 链接用 LinkAnnotation.Url，Text 自动通过 LocalUriHandler 打开；longPress 不被消费
 *   传给父层（ChatScreen 长按触点弹菜单）
 * - 流式期间 80ms 节流（保留既有 UI 节奏），closeUnclosedFences 处理未闭合围栏
 *
 * 对齐 web streaming-markdown (smd) 的增量渲染思路：把 O(n²) 全量解析降为
 * O(段落大小) per delta。
 */
@Composable
fun MarkdownText(
    markdown: String,
    modifier: Modifier = Modifier,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodyMedium,
    streaming: Boolean = false,
) {
    val textColor = if (style.color != Color.Unspecified) style.color else MaterialTheme.colorScheme.onSurface
    val linkColor = MaterialTheme.colorScheme.primary

    val effectiveMarkdown = if (streaming) closeUnclosedFences(markdown) else markdown

    val blocks = remember(effectiveMarkdown) { splitBlocks(effectiveMarkdown) }

    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        blocks.forEach { block ->
            when (block.type) {
                BlockType.PARAGRAPH -> {
                    val annotated = remember(block.text) {
                        parseInlineParagraph(block.text, textColor, linkColor)
                    }
                    Text(text = annotated, style = style)
                }
                BlockType.CODE_FENCE -> {
                    CodeBlockSurface(text = block.text, baseTextColor = textColor)
                }
            }
        }
    }
}
