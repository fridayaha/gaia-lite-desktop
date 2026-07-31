import { describe, it, expect } from '@ohos/hypium';
import { formatRelativeTime } from '../../../main/ets/util/TimeFormat';

/**
 * formatRelativeTime 单元测试。复刻 Android TimeFormatTest.kt。
 */
export function registerTimeFormatTests(): void {
  describe('TimeFormat', () => {
    it('just now (< 60s)', 0, () => {
      const iso = new Date(Date.now() - 30000).toISOString()
      expect(formatRelativeTime(iso)).assertEqual('刚刚')
    })

    it('minutes ago', 0, () => {
      const iso = new Date(Date.now() - 5 * 60 * 1000).toISOString()
      expect(formatRelativeTime(iso)).assertEqual('5 分钟前')
    })

    it('hours ago', 0, () => {
      const iso = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()
      expect(formatRelativeTime(iso)).assertEqual('3 小时前')
    })

    it('yyyy-MM-dd for > 24h', 0, () => {
      const d = new Date(Date.now() - 48 * 60 * 60 * 1000)
      const iso = d.toISOString()
      const result = formatRelativeTime(iso)
      const y = d.getFullYear()
      const m = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      expect(result).assertEqual(`${y}-${m}-${day}`)
    })

    it('empty string returns itself', 0, () => {
      expect(formatRelativeTime('')).assertEqual('')
    })

    it('invalid string returns itself', 0, () => {
      expect(formatRelativeTime('not-a-date')).assertEqual('not-a-date')
    })
  })
}
