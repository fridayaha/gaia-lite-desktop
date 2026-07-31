package com.unionagents.enduser.repo

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * SemVer 版本比较工具测试。
 */
class VersionUtilTest {

    @Test
    fun `returns true when latest is newer`() {
        assertTrue(VersionUtil.isVersionNewer("0.8.209", "0.8.208"))
        assertTrue(VersionUtil.isVersionNewer("0.9.0", "0.8.209"))
        assertTrue(VersionUtil.isVersionNewer("1.0.0", "0.8.209"))
    }

    @Test
    fun `returns false when latest is older or equal`() {
        assertFalse(VersionUtil.isVersionNewer("0.8.208", "0.8.209"))
        assertFalse(VersionUtil.isVersionNewer("0.8.209", "0.8.209"))
        assertFalse(VersionUtil.isVersionNewer("0.7.0", "0.8.209"))
    }

    @Test
    fun `ignores non-digit suffixes`() {
        assertFalse(VersionUtil.isVersionNewer("0.8.209-beta", "0.8.209"))
        assertTrue(VersionUtil.isVersionNewer("0.8.210-beta", "0.8.209"))
    }

    @Test
    fun `trims whitespace`() {
        assertFalse(VersionUtil.isVersionNewer(" 0.8.209 ", "0.8.209"))
        assertTrue(VersionUtil.isVersionNewer(" 0.8.210 ", "0.8.209"))
    }

    @Test
    fun `handles different part lengths`() {
        assertFalse(VersionUtil.isVersionNewer("0.8", "0.8.0"))
        assertTrue(VersionUtil.isVersionNewer("0.8.1", "0.8"))
    }

    @Test
    fun `returns false for blank inputs`() {
        assertFalse(VersionUtil.isVersionNewer("", "0.8.209"))
        assertFalse(VersionUtil.isVersionNewer("0.8.209", ""))
        assertFalse(VersionUtil.isVersionNewer("   ", "0.8.209"))
    }
}
