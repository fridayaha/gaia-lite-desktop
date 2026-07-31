package com.unionagents.enduser.repo

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateBadgeStoreTest {

    @Test
    fun `shows when latest newer and unseen`() {
        assertTrue(shouldShowUpdateBadge("0.8.237", "0.8.236", null))
        assertTrue(shouldShowUpdateBadge("0.8.237", "0.8.236", "0.8.236"))
    }

    @Test
    fun `hides when up to date or older`() {
        assertFalse(shouldShowUpdateBadge("0.8.236", "0.8.236", null))
        assertFalse(shouldShowUpdateBadge("0.8.235", "0.8.236", null))
    }

    @Test
    fun `hides when latest already seen`() {
        assertFalse(shouldShowUpdateBadge("0.8.237", "0.8.236", "0.8.237"))
    }

    @Test
    fun `shows again when newer than seen`() {
        assertTrue(shouldShowUpdateBadge("0.8.238", "0.8.236", "0.8.237"))
    }

    @Test
    fun `hides when never checked`() {
        assertFalse(shouldShowUpdateBadge(null, "0.8.236", null))
    }
}
