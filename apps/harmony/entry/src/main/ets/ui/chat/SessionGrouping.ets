import { Session } from '../../net/dto/ChatDtos';

/**
 * 会话按日期分组。复刻 Android ui/chat/SessionGrouping.kt。
 * 标签：今天 / 昨天 / 本周 / 上周 / 更早；组内保持上游（时间倒序）顺序。
 * 时间戳取 stableLastAt，缺失退 stableCreatedAt；单位秒（epoch seconds）。
 */
export class SessionGroup {
  label: string = ''
  items: Session[] = []

  constructor(label: string, items: Session[]) {
    this.label = label
    this.items = items
  }
}

const DAY_MS = 86400000

export function groupSessionsByDate(sessions: Session[], nowMs: number = Date.now()): SessionGroup[] {
  if (sessions.length === 0) return []
  const now = new Date(nowMs)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterdayStart = todayStart - DAY_MS
  // 本周一起点（周一为一周开始；当天周一则取当天 0 点）
  const daysSinceMonday = (now.getDay() + 6) % 7
  const weekStart = todayStart - daysSinceMonday * DAY_MS
  const lastWeekStart = weekStart - 7 * DAY_MS

  const groups: SessionGroup[] = []
  for (const s of sessions) {
    const sec = s.stableLastAt ?? s.stableCreatedAt
    const ts = sec > 0 ? sec * 1000 : todayStart
    let label = '更早'
    if (ts >= todayStart) label = '今天'
    else if (ts >= yesterdayStart) label = '昨天'
    else if (ts >= weekStart) label = '本周'
    else if (ts >= lastWeekStart) label = '上周'
    if (groups.length === 0 || groups[groups.length - 1].label !== label) {
      groups.push(new SessionGroup(label, [s]))
    } else {
      groups[groups.length - 1].items.push(s)
    }
  }
  return groups
}

/**
 * 相对时间文案（"1分钟" / "5分钟" / "3小时" / "2天"）。
 * 复刻 Android SessionGrouping.formatRelativeTime（秒级 epoch 入参）。
 */
export function formatSessionTime(seconds: number | null, nowMs: number = Date.now()): string {
  if (seconds === null || seconds <= 0) return ''
  const diffMs = Math.max(0, nowMs - seconds * 1000)
  const minMs = 60000
  const hrMs = 3600000
  if (diffMs < minMs) return '1分钟'
  if (diffMs < hrMs) return `${Math.floor(diffMs / minMs)}分钟`
  if (diffMs < DAY_MS) return `${Math.floor(diffMs / hrMs)}小时`
  return `${Math.floor(diffMs / DAY_MS)}天`
}
