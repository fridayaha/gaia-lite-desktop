package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.net.dto.Session
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class SessionGroupingTest {

    // 固定 now，让测试可复现
    private val now = ZonedDateTime.of(
        2026, 7, 18, 14, 30, 0, 0, ZoneId.of("Asia/Shanghai"),
    )

    private fun sessionAt(epochSec: Long, title: String = "test") = Session(
        sessionId = "s-$epochSec",
        title = title,
        lastMessageAt = epochSec.toDouble(),
    )

    @Test
    fun `groups today yesterday this week last week earlier correctly`() {
        // now = 2026-07-18 14:30 (Saturday)
        val today = now.toEpochSecond()
        val yesterday = today - 86_400
        val twoDaysAgo = today - 2 * 86_400 // 2026-07-16 → 本周（周一=2026-07-13）
        val eightDaysAgo = today - 8 * 86_400 // 上周
        val thirtyDaysAgo = today - 30 * 86_400 // 更早

        val sessions = listOf(
            sessionAt(thirtyDaysAgo),
            sessionAt(eightDaysAgo),
            sessionAt(twoDaysAgo),
            sessionAt(yesterday),
            sessionAt(today),
        )
        val groups = groupSessionsByDate(sessions, now)
        val labels = groups.map { it.label }
        assertEquals(listOf("更早", "上周", "本周", "昨天", "今天"), labels)
    }

    @Test
    fun `groups preserve input order within each group`() {
        val today = now.toEpochSecond()
        // 两条同属今天，按入参顺序排
        val sessions = listOf(
            sessionAt(today - 60, title = "later"),
            sessionAt(today - 600, title = "earlier"),
        )
        val groups = groupSessionsByDate(sessions, now)
        assertEquals(1, groups.size)
        assertEquals("今天", groups[0].label)
        assertEquals(listOf("later", "earlier"), groups[0].items.map { it.title })
    }

    @Test
    fun `falls back to created_at when last_message_at missing`() {
        val today = now.toEpochSecond()
        val session = Session(
            sessionId = "s1",
            title = "no last",
            createdAt = today.toDouble(),
            lastMessageAt = null,
        )
        val groups = groupSessionsByDate(listOf(session), now)
        assertEquals(1, groups.size)
        assertEquals("今天", groups[0].label)
    }

    @Test
    fun `missing timestamp defaults to today`() {
        // 0 timestamp → 视为今天（不会崩，不会归到更早）
        val session = Session(sessionId = "x", title = "empty", createdAt = 0.0, lastMessageAt = null)
        val groups = groupSessionsByDate(listOf(session), now)
        assertEquals("今天", groups[0].label)
    }

    @Test
    fun `empty sessions returns empty list`() {
        assertTrue(groupSessionsByDate(emptyList(), now).isEmpty())
    }

    @Test
    fun `consecutive sessions in same label merge into single group`() {
        val today = now.toEpochSecond()
        val sessions = listOf(
            sessionAt(today - 60, "a"),
            sessionAt(today - 120, "b"),
            sessionAt(today - 2 * 86_400, "c"), // 本周
        )
        val groups = groupSessionsByDate(sessions, now)
        assertEquals(2, groups.size)
        assertEquals(listOf("a", "b"), groups[0].items.map { it.title })
        assertEquals(listOf("c"), groups[1].items.map { it.title })
    }

    @Test
    fun `formatRelativeTime returns 1分钟 for less than 60s`() {
        assertEquals("1分钟", formatRelativeTime(now.toEpochSecond() - 30.0, now))
    }

    @Test
    fun `formatRelativeTime returns N分钟 for sub-hour`() {
        assertEquals("5分钟", formatRelativeTime(now.toEpochSecond() - 300.0, now))
    }

    @Test
    fun `formatRelativeTime returns N小时 for sub-day`() {
        assertEquals("3小时", formatRelativeTime(now.toEpochSecond() - 3 * 3600.0, now))
    }

    @Test
    fun `formatRelativeTime returns N天 for beyond day`() {
        assertEquals("2天", formatRelativeTime(now.toEpochSecond() - 2 * 86_400.0, now))
    }

    @Test
    fun `formatRelativeTime returns empty for null or non-positive`() {
        assertEquals("", formatRelativeTime(null, now))
        assertEquals("", formatRelativeTime(0.0, now))
        assertEquals("", formatRelativeTime(-5.0, now))
    }
}
