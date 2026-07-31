package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.net.dto.Session
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.TemporalAdjusters

/**
 * 会话按日期分组的标签 + 该组的会话列表。
 * 镜像 apps/enduser/src/components/chat/ChatSessionList.vue 的 groupedSessions。
 */
data class SessionGroup(
    val label: String,
    val items: List<Session>,
)

/**
 * 按今天/昨天/本周/上周/更早 给会话分组。每组内部保持原顺序（期望上游已按时间倒序排好）。
 *
 * 时间戳取 [Session.stableLastAt]，缺失时退到 [Session.stableCreatedAt]。
 * 单位：秒（epoch seconds），与 Hermes/Dify 返回的 last_message_at / created_at 一致。
 */
fun groupSessionsByDate(
    sessions: List<Session>,
    now: ZonedDateTime = ZonedDateTime.now(),
): List<SessionGroup> {
    if (sessions.isEmpty()) return emptyList()

    val todayStart = now.toLocalDate().atStartOfDay(now.zone)
    val yesterdayStart = todayStart.minusDays(1)
    val weekStart = todayStart.with(TemporalAdjusters.previousOrSame(java.time.DayOfWeek.MONDAY))
    val lastWeekStart = weekStart.minusWeeks(1)

    val groups = mutableListOf<SessionGroup>()
    var cur: SessionGroup? = null
    for (s in sessions) {
        val ts = (s.stableLastAt ?: s.stableCreatedAt).takeIf { it > 0 }?.let {
            Instant.ofEpochSecond(it.toLong()).atZone(now.zone)
        } ?: todayStart
        val label = when {
            ts >= todayStart -> "今天"
            ts >= yesterdayStart -> "昨天"
            ts >= weekStart -> "本周"
            ts >= lastWeekStart -> "上周"
            else -> "更早"
        }
        if (cur?.label != label) {
            cur = SessionGroup(label, mutableListOf())
            groups.add(cur)
        }
        (cur!!.items as MutableList).add(s)
    }
    return groups
}

/**
 * 相对时间文案（"1分钟" / "5分钟" / "3小时" / "2天"）。
 * 镜像 ChatSessionList.vue 的 formatTime。
 */
fun formatRelativeTime(
    seconds: Double?,
    now: ZonedDateTime = ZonedDateTime.now(),
): String {
    if (seconds == null || seconds <= 0.0) return ""
    val ts = Instant.ofEpochSecond(seconds.toLong()).atZone(now.zone)
    val diffMillis = java.time.Duration.between(ts, now).toMillis().coerceAtLeast(0L)
    val min = 60_000L
    val hr = 3_600_000L
    val day = 86_400_000L
    return when {
        diffMillis < min -> "1分钟"
        diffMillis < hr -> "${diffMillis / min}分钟"
        diffMillis < day -> "${diffMillis / hr}小时"
        else -> "${diffMillis / day}天"
    }
}
