import { describe, it, expect } from '@ohos/hypium';
import { MessageFeedbackRepository } from '../../../main/ets/repo/MessageFeedbackRepository';
import { Message } from '../../../main/ets/net/dto/ChatDtos';

/**
 * MessageFeedbackRepository.messageRefOf 单元测试。复刻 Android MessageFeedbackRepositoryTest.kt 的纯逻辑分支。
 * 只测同步 hash 路径 + mid: 路径，不测真实的 sha256Sync（依赖 @ohos.buffer API）。
 */
export function registerMessageFeedbackRefTests(): void {
  describe('MessageFeedbackRepository.messageRefOf', () => {
    it('engine id → mid: prefix', 0, () => {
      const m = new Message()
      m.id = 42
      m.content = 'hello'
      const ref = MessageFeedbackRepository.messageRefOf(m)
      expect(ref).assertEqual('mid:42')
    })

    it('null id with content → hash: prefix', 0, () => {
      const m = new Message()
      m.id = null
      m.content = 'some content'
      const ref = MessageFeedbackRepository.messageRefOf(m)
      expect(ref.startsWith('hash:')).assertTrue()
      expect(ref.length > 'hash:'.length).assertTrue()
    })

    it('local id (Date.now()) → hash: prefix', 0, () => {
      const m = new Message()
      m.id = Date.now()
      m.content = 'user-typed'
      const ref = MessageFeedbackRepository.messageRefOf(m)
      expect(ref.startsWith('hash:')).assertTrue()
    })

    it('same content produces same hash', 0, () => {
      const m1 = new Message()
      m1.id = null
      m1.content = 'identical'
      const m2 = new Message()
      m2.id = null
      m2.content = 'identical'
      const r1 = MessageFeedbackRepository.messageRefOf(m1)
      const r2 = MessageFeedbackRepository.messageRefOf(m2)
      expect(r1).assertEqual(r2)
    })

    it('different content produces different hash', 0, () => {
      const m1 = new Message()
      m1.id = null
      m1.content = 'content-a'
      const m2 = new Message()
      m2.id = null
      m2.content = 'content-b'
      const r1 = MessageFeedbackRepository.messageRefOf(m1)
      const r2 = MessageFeedbackRepository.messageRefOf(m2)
      expect(r1).assertNotEqual(r2)
    })

    it('empty content produces a stable hash too', 0, () => {
      const m = new Message()
      m.id = null
      m.content = ''
      const ref = MessageFeedbackRepository.messageRefOf(m)
      expect(ref.startsWith('hash:')).assertTrue()
    })

    it('engine id wins over hash (mid: takes precedence)', 0, () => {
      const m = new Message()
      m.id = 100
      m.content = 'whatever'
      expect(MessageFeedbackRepository.messageRefOf(m)).assertEqual('mid:100')
    })
  })
}
