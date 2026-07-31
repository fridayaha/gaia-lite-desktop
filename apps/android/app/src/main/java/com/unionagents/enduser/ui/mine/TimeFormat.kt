package com.unionagents.enduser.ui.mine

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** ISO 时间 → 相对时间（刚刚 / N 分钟前 / N 小时前 / yyyy-MM-dd）。解析失败原样返回。 */
fun formatRelativeTime(iso: String): String {
    return try {
        val t = Instant.parse(iso)
        val now = Instant.now()
        val mins = ChronoUnit.MINUTES.between(t, now)
        when {
            mins < 1 -> "刚刚"
            mins < 60 -> "$mins 分钟前"
            mins < 24 * 60 -> "${mins / 60} 小时前"
            else -> {
                val date = t.atZone(ZoneId.systemDefault()).toLocalDate()
                DateTimeFormatter.ISO_LOCAL_DATE.format(date)
            }
        }
    } catch (_: Throwable) {
        iso
    }
}
