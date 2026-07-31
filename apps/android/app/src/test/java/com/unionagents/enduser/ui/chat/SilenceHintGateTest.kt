package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.net.dto.ToolCallState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SilenceHintGateTest {

    private fun tool(completed: Boolean) = ToolCallState(
        name = "write_file",
        preview = "",
        toolCallId = null,
        completed = completed,
        error = null,
    )

    private val approval = ApprovalState("r1", "cmd", "desc", listOf("once", "deny"))

    @Test
    fun `shows when no tool and no approval`() {
        assertTrue(shouldShowSilenceHint(emptyList(), null))
    }

    @Test
    fun `shows when all tools completed`() {
        assertTrue(shouldShowSilenceHint(listOf(tool(completed = true), tool(completed = true)), null))
    }

    @Test
    fun `hides while any tool executing`() {
        assertFalse(shouldShowSilenceHint(listOf(tool(completed = false)), null))
        assertFalse(shouldShowSilenceHint(listOf(tool(completed = true), tool(completed = false)), null))
    }

    @Test
    fun `hides while approval pending`() {
        assertFalse(shouldShowSilenceHint(emptyList(), approval))
        assertFalse(shouldShowSilenceHint(listOf(tool(completed = true)), approval))
    }

    @Test
    fun `shows again after tool completes and approval cleared`() {
        assertTrue(shouldShowSilenceHint(listOf(tool(completed = true)), null))
    }
}
