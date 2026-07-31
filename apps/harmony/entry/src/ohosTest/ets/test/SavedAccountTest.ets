import { describe, it, expect } from '@ohos/hypium';
import { SavedAccount } from '../../../main/ets/repo/AccountStore';

/**
 * SavedAccount 序列化/反序列化单元测试。
 * 验证 fromJson/toJson 往返一致性 + 可选字段处理。
 */
export function registerSavedAccountTests(): void {
  describe('AccountStore.SavedAccount', () => {
    it('fromJson populates required fields', 0, () => {
      const o: Record<string, Object> = {
        id: 'user-1',
        username: 'alice',
        access_token: 'tok-1',
        refresh_token: 'ref-1',
        saved_at: 1718000000000,
      }
      const acc = SavedAccount.fromJson(o)
      expect(acc.id).assertEqual('user-1')
      expect(acc.username).assertEqual('alice')
      expect(acc.access_token).assertEqual('tok-1')
      expect(acc.refresh_token).assertEqual('ref-1')
      expect(acc.saved_at).assertEqual(1718000000000)
    })

    it('fromJson includes optional email/avatar', 0, () => {
      const o: Record<string, Object> = {
        id: 'u2',
        username: 'bob',
        email: 'bob@example.com',
        avatar_url: 'https://example.com/a.png',
        access_token: 'tok',
        refresh_token: 'ref',
        saved_at: 1,
      }
      const acc = SavedAccount.fromJson(o)
      expect(acc.email).assertEqual('bob@example.com')
      expect(acc.avatar_url).assertEqual('https://example.com/a.png')
    })

    it('fromJson tolerates missing optional fields', 0, () => {
      const o: Record<string, Object> = {
        id: 'u3',
        username: 'carol',
        access_token: 'tok',
        refresh_token: 'ref',
        saved_at: 1,
      }
      const acc = SavedAccount.fromJson(o)
      expect(acc.email).assertNull()
      expect(acc.avatar_url).assertNull()
    })

    it('fromJson tolerates wrong types (falls back to defaults)', 0, () => {
      const o: Record<string, Object> = {
        id: 123,
        username: 42,
        access_token: true,
        refresh_token: null,
        saved_at: 'oops',
      }
      const acc = SavedAccount.fromJson(o)
      expect(acc.id).assertEqual('')
      expect(acc.username).assertEqual('')
      expect(acc.access_token).assertEqual('')
      expect(acc.refresh_token).assertEqual('')
      expect(acc.saved_at).assertEqual(0)
    })

    it('toJson roundtrip preserves all fields', 0, () => {
      const a = new SavedAccount()
      a.id = 'rt-1'
      a.username = 'dave'
      a.email = 'dave@ex.com'
      a.avatar_url = 'https://ex/dave.png'
      a.access_token = 'tok'
      a.refresh_token = 'ref'
      a.saved_at = 999
      const o = a.toJson()
      const b = SavedAccount.fromJson(o)
      expect(b.id).assertEqual(a.id)
      expect(b.username).assertEqual(a.username)
      expect(b.email).assertEqual(a.email)
      expect(b.avatar_url).assertEqual(a.avatar_url)
      expect(b.access_token).assertEqual(a.access_token)
      expect(b.refresh_token).assertEqual(a.refresh_token)
      expect(b.saved_at).assertEqual(a.saved_at)
    })

    it('toJson omits null email/avatar', 0, () => {
      const a = new SavedAccount()
      a.id = 'rt-2'
      a.username = 'eve'
      a.access_token = 'tok'
      a.refresh_token = 'ref'
      a.saved_at = 1
      const o = a.toJson()
      expect(o['email'] === undefined).assertTrue()
      expect(o['avatar_url'] === undefined).assertTrue()
    })
  })
}
