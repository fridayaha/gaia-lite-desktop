package com.unionagents.enduser.repo

/**
 * SemVer "X.Y.Z" 比较：返回 latest 是否比 current 新。
 * 非数字段按 0 处理；长度不一致按 0 补齐（"0.8" 等价 "0.8.0"）。
 */
object VersionUtil {
    fun isVersionNewer(latest: String, current: String): Boolean {
        val l = latest.trim()
        val c = current.trim()
        if (l.isBlank() || c.isBlank()) return false
        val latestParts = l.split('.').map { it.filter { ch -> ch.isDigit() }.toIntOrNull() ?: 0 }
        val currentParts = c.split('.').map { it.filter { ch -> ch.isDigit() }.toIntOrNull() ?: 0 }
        val maxLen = maxOf(latestParts.size, currentParts.size)
        for (i in 0 until maxLen) {
            val lv = latestParts.getOrElse(i) { 0 }
            val cv = currentParts.getOrElse(i) { 0 }
            if (lv > cv) return true
            if (lv < cv) return false
        }
        return false
    }
}
