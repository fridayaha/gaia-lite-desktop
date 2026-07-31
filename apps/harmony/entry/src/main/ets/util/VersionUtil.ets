/**
 * 版本号比较：判断 newer 是否比 current 更新。
 * 复刻 Android repo/VersionUtil.kt 的 isVersionNewer。
 *
 * 规则：按点分段，优先比较数字段（按数字大小），无数字段时按字符串字典序。
 * 例：1.10.0 比 1.9.0 新；1.0.0-beta.1 比 1.0.0 旧（预发布后缀比发布版旧）。
 */
export function isVersionNewer(newer: string, current: string): boolean {
  if (newer.length === 0) return false
  if (current.length === 0) return true
  if (newer === current) return false

  // 拆预发布后缀
  const newerParts = splitVersion(newer)
  const currentParts = splitVersion(current)

  const len = Math.max(newerParts.numeric.length, currentParts.numeric.length)
  for (let i = 0; i < len; i++) {
    const n = newerParts.numeric[i] ?? 0
    const c = currentParts.numeric[i] ?? 0
    if (n !== c) return n > c
  }
  // 主版本相同：有预发布后缀的比无后缀的旧
  if (newerParts.prerelease.length === 0 && currentParts.prerelease.length > 0) return true
  if (newerParts.prerelease.length > 0 && currentParts.prerelease.length === 0) return false
  // 同有后缀：字典序比较
  if (newerParts.prerelease === currentParts.prerelease) return false
  return newerParts.prerelease > currentParts.prerelease
}

interface SplitVersionResult {
  numeric: number[]
  prerelease: string
}

function splitVersion(v: string): SplitVersionResult {
  const dashIdx = v.indexOf('-')
  const main = dashIdx >= 0 ? v.slice(0, dashIdx) : v
  const pre = dashIdx >= 0 ? v.slice(dashIdx + 1) : ''
  const parts = main.split('.')
  const numeric: number[] = []
  for (const p of parts) {
    const n = parseInt(p, 10)
    numeric.push(Number.isNaN(n) ? 0 : n)
  }
  const result: SplitVersionResult = { numeric, prerelease: pre }
  return result
}
