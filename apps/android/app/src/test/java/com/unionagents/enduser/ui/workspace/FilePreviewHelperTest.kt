package com.unionagents.enduser.ui.workspace

import com.unionagents.enduser.net.dto.WorkspaceFileContent
import org.junit.Assert.assertEquals
import org.junit.Test

class FilePreviewHelperTest {

    @Test
    fun `previewType detects image markdown html pdf and office by flags or extension`() {
        assertEquals(
            PreviewType.Image,
            WorkspaceFileContent(path = "a.png", name = "a.png", size = 0, isImage = true).previewType(),
        )
        assertEquals(
            PreviewType.Markdown,
            WorkspaceFileContent(path = "a.md", name = "a.md", size = 0, isMarkdown = true).previewType(),
        )
        assertEquals(
            PreviewType.Html,
            WorkspaceFileContent(path = "a.html", name = "a.html", size = 0).previewType(),
        )
        assertEquals(
            PreviewType.Pdf,
            WorkspaceFileContent(path = "a.pdf", name = "a.pdf", size = 0).previewType(),
        )
        assertEquals(
            PreviewType.Office,
            WorkspaceFileContent(path = "a.docx", name = "a.docx", size = 0).previewType(),
        )
    }

    @Test
    fun `previewType falls back to text when content present`() {
        assertEquals(
            PreviewType.Text,
            WorkspaceFileContent(path = "a.txt", name = "a.txt", size = 0, isText = true).previewType(),
        )
        assertEquals(
            PreviewType.Text,
            WorkspaceFileContent(path = "a.log", name = "a.log", size = 0, content = "hello").previewType(),
        )
    }

    @Test
    fun `previewType returns unknown for unsupported binary`() {
        assertEquals(
            PreviewType.Unknown,
            WorkspaceFileContent(path = "a.bin", name = "a.bin", size = 0).previewType(),
        )
    }

    @Test
    fun `fileExtension extracts lowercase extension`() {
        assertEquals("png", "image.PNG".fileExtension())
        assertEquals("pdf", "folder/a.PDF".fileExtension())
        assertEquals("", "noext".fileExtension())
    }

    @Test
    fun `mimeTypeForExtension maps common office and image types`() {
        assertEquals("application/pdf", "a.pdf".mimeTypeForExtension())
        assertEquals(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "a.docx".mimeTypeForExtension(),
        )
        assertEquals("image/jpeg", "a.jpg".mimeTypeForExtension())
        assertEquals("application/octet-stream", "a.unknown".mimeTypeForExtension())
    }

    @Test
    fun `decodeTextContent prefers plain content over base64`() {
        val encoded = java.util.Base64.getEncoder().encodeToString("base64".toByteArray())
        val content = WorkspaceFileContent(
            path = "a.txt",
            name = "a.txt",
            size = 0,
            content = "plain",
            contentB64 = encoded,
        )
        assertEquals("plain", content.decodeTextContent())
    }

    @Test
    fun `decodeTextContent decodes base64 when plain content null`() {
        val encoded = java.util.Base64.getEncoder().encodeToString("hello".toByteArray())
        val content = WorkspaceFileContent(
            path = "a.txt",
            name = "a.txt",
            size = 0,
            contentB64 = encoded,
        )
        assertEquals("hello", content.decodeTextContent())
    }
}
