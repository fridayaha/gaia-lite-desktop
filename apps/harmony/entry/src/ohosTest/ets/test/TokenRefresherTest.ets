import { describe, it, expect } from '@ohos/hypium';
import { TokenRefresher } from '../../../main/ets/net/TokenRefresher';
import { TokenStorage, TokenData } from '../../../main/ets/data/TokenStorage';

/**
 * TokenRefresher 单元测试。复刻 Android TokenRefresherTest.kt 的 Mutex 单飞语义。
 */
export function registerTokenRefresherTests(): void {
  describe('TokenRefresher', () => {
    class FakeStorage implements TokenStorage {
      saved: TokenData[] = []
      access: string | null = null
      refresh: string | null = null
      async save(token: TokenData): Promise<void> { this.saved.push(token); this.access = token.accessToken; this.refresh = token.refreshToken }
      async get(): Promise<TokenData | null> { return this.access !== null ? new TokenData(this.access, this.refresh ?? '') : null }
      async getAccessToken(): Promise<string | null> { return this.access }
      async getRefreshToken(): Promise<string | null> { return this.refresh }
      async clear(): Promise<void> { this.access = null; this.refresh = null }
      async refresh(): Promise<string | null> { return null }
    }

    it('returns new access on success', 0, async () => {
      const storage = new FakeStorage()
      storage.access = 'old-access'
      storage.refresh = 'old-refresh'
      const executor = async (_r: string) => ({ access: 'new-access', refresh: 'new-refresh' })
      const r = new TokenRefresher(storage, executor)
      const out = await r.refresh()
      expect(out).assertEqual('new-access')
      expect(storage.access).assertEqual('new-access')
      expect(storage.refresh).assertEqual('new-refresh')
    })

    it('returns null when storage has no refresh token', 0, async () => {
      const storage = new FakeStorage()
      storage.access = null
      storage.refresh = null
      const r = new TokenRefresher(storage, async () => ({ access: 'should-not-reach', refresh: 'x' }))
      const out = await r.refresh()
      expect(out).assertEqual(null)
    })

    it('returns null when executor returns null', 0, async () => {
      const storage = new FakeStorage()
      storage.access = 'old-access'
      storage.refresh = 'old-refresh'
      const r = new TokenRefresher(storage, async () => null)
      const out = await r.refresh()
      expect(out).assertEqual(null)
    })

    it('falls back to old refresh token when response has none', 0, async () => {
      const storage = new FakeStorage()
      storage.access = 'old-access'
      storage.refresh = 'old-refresh'
      const r = new TokenRefresher(storage, async () => ({ access: 'new-access', refresh: null }))
      await r.refresh()
      expect(storage.refresh).assertEqual('old-refresh')
    })

    it('single-flights concurrent refresh calls', 0, async () => {
      const storage = new FakeStorage()
      storage.access = 'old-access'
      storage.refresh = 'old-refresh'
      let calls = 0
      const r = new TokenRefresher(storage, async (_r: string) => {
        calls++
        await new Promise((res) => setTimeout(res, 50))
        return { access: 'new-access', refresh: 'new-refresh' }
      })
      // 并发 3 次 refresh：应合并为 1 次实际调用
      const results = await Promise.all([r.refresh(), r.refresh(), r.refresh()])
      expect(results[0]).assertEqual('new-access')
      expect(results[1]).assertEqual('new-access')
      expect(results[2]).assertEqual('new-access')
      expect(calls).assertEqual(1)
    })
  })
}
