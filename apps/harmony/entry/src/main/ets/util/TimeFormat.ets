/**
 * ISO 时间 → 相对时间（刚刚 / N 分钟前 / N 小时前 / yyyy-MM-dd）。
 * 复刻 Android ui/mine/TimeFormat.kt。
 * 解析失败原样返回。
 */
export function formatRelativeTime(iso: string): string {
  const t = parseIso(iso)
  if (t === null) return iso
  const now = Date.now()
  const diffMs = now - t
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)} 小时前`
  const d = new Date(t)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/**
 * 解析 ISO 字符串为毫秒时间戳。失败返回 null。
 * 支持 Z 结尾、+08:00 时区偏移、不含时区的本地时间。
 */
function parseIso(iso: string): number | null {
  if (iso.length === 0) return null
  // 标准 new Date 解析支持 ISO 8601 含 Z 和时区偏移
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return t
}
