import buffer from '@ohos.buffer';

/**
 * JWT 解析工具：解析三段式 JWT 的 exp 字段（UTC 秒级时间戳）。失败返回 null。
 * 复刻 Android net/JwtUtil.kt 语义，便于 SessionController 启动期判过期。
 */
export function parseExp(token: string): number | null {
  const parts = token.split('.')
  if (parts.length < 2) return null
  try {
    const payload = buffer.from(parts[1], 'base64url').toString('utf-8')
    const obj: Record<string, Object> = JSON.parse(payload) as Record<string, Object>
    const exp = obj['exp']
    if (typeof exp === 'number' && exp > 0) return exp
    return null
  } catch (e) {
    return null
  }
}
