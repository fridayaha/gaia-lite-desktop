import { describe, it, expect } from '@ohos/hypium';
import { parseExp } from '../../../main/ets/util/JwtUtil';

/**
 * parseExp 单元测试。复刻 Android JwtUtilTest.kt。
 * JWT exp 解析 + Base64 URL decode。
 */
export function registerJwtUtilTests(): void {
  describe('JwtUtil', () => {
    /**
     * 构造一个 JWT payload（Base64URL encoded）：{ "exp": <exp> }
     * Header 占位 "eyJhbGciOiJub25lIg" = {"alg":"none"}
     */
    function makeJwt(exp: number): string {
      const payloadObj = { exp: exp }
      const json = JSON.stringify(payloadObj)
      // Base64URL encode（无 padding）
      const b64 = base64UrlEncode(json)
      return `eyJhbGciOiJub25lIg.${b64}.signature`
    }

    it('parses valid exp', 0, () => {
      const exp = Math.floor(Date.now() / 1000) + 3600
      const token = makeJwt(exp)
      const parsed = parseExp(token)
      expect(parsed).assertEqual(exp)
    })

    it('returns null for non-jwt string', 0, () => {
      expect(parseExp('not-a-jwt')).assertEqual(null)
    })

    it('returns null for empty payload', 0, () => {
      expect(parseExp('header.signature')).assertEqual(null)
    })

    it('returns null for invalid base64 payload', 0, () => {
      expect(parseExp('header.!!!invalid-base64!!!.sig')).assertEqual(null)
    })

    it('returns null for missing exp field', 0, () => {
      // payload: { "sub": "test" } 不含 exp
      const json = JSON.stringify({ sub: 'test' })
      const b64 = base64UrlEncode(json)
      const token = `header.${b64}.sig`
      expect(parseExp(token)).assertEqual(null)
    })

    it('returns null for zero or negative exp', 0, () => {
      const json = JSON.stringify({ exp: 0 })
      const b64 = base64UrlEncode(json)
      const token = `header.${b64}.sig`
      expect(parseExp(token)).assertEqual(null)
    })
  })
}

function base64UrlEncode(s: string): string {
  // 简化实现：用 Buffer base64 → 替换 +/= 为 - _ 等价 / 去掉 padding
  const b64 = btoa(s)
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

// 在 ArkTS 环境里 btoa 可能不可用；这里降级用 @ohos.buffer
function btoa(s: string): string {
  // 退路：手工 base64 编码
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  let out = ''
  let i = 0
  while (i < s.length) {
    const b1 = s.charCodeAt(i++) & 0xff
    const b2 = i < s.length ? (s.charCodeAt(i++) & 0xff) : -1
    const b3 = i < s.length ? (s.charCodeAt(i++) & 0xff) : -1
    out += chars[b1 >> 2]
    out += chars[((b1 & 0x03) << 4) | (b2 >= 0 ? (b2 >> 4) : 0)]
    out += b2 >= 0 ? chars[((b2 & 0x0f) << 2) | (b3 >= 0 ? (b3 >> 6) : 0)] : '='
    out += b3 >= 0 ? chars[b3 & 0x3f] : '='
  }
  return out
}
