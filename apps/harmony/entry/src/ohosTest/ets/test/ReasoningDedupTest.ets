import { describe, it, expect } from '@ohos/hypium';
import { isReasoningEchoOfReply } from '../../../main/ets/sse/ReasoningDedup';

/**
 * reasoning.available 去重判定单元测试。复刻 Android ReasoningDedupTest.kt。
 */
export function registerReasoningDedupTests(): void {
  describe('ReasoningDedup', () => {
    it('identical text is echo', 0, () => {
      expect(isReasoningEchoOfReply('当前目录是空的。', '当前目录是空的。')).assertTrue()
    })

    it('reasoning as prefix of reply is echo', 0, () => {
      const reply = '当前目录是 /root，目录是空的，没有任何文件。'
      expect(isReasoningEchoOfReply(reply.slice(0, 20), reply)).assertTrue()
    })

    it('reasoning as suffix of accumulated stream is echo', 0, () => {
      const turn1 = '我先查看一下当前目录的内容。'
      const turn2 = '当前目录是空的。'
      const accumulated = turn1 + '\n\n' + turn2
      expect(isReasoningEchoOfReply(turn2, accumulated)).assertTrue()
      expect(isReasoningEchoOfReply(turn1, accumulated)).assertTrue()
    })

    it('truncated reasoning contained mid-stream is echo', 0, () => {
      const full = '先说一句。' + '很长的正文'.repeat(200)
      const blob = full.slice(0, 500)
      expect(isReasoningEchoOfReply(blob, full + '结尾补充')).assertTrue()
    })

    it('reply prefix of reasoning is echo', 0, () => {
      expect(isReasoningEchoOfReply('当前目录是空的，没有任何文件。', '当前目录是空的')).assertTrue()
    })

    it('genuinely different reasoning is kept', 0, () => {
      const reasoning = '用户想看目录内容，我应该调用 terminal 工具执行 ls。'
      const reply = '当前目录是空的。'
      expect(isReasoningEchoOfReply(reasoning, reply)).assertFalse()
    })

    it('empty inputs are kept', 0, () => {
      expect(isReasoningEchoOfReply('', '回复')).assertFalse()
      expect(isReasoningEchoOfReply('思考', '')).assertFalse()
      expect(isReasoningEchoOfReply('', '')).assertFalse()
    })

    it('whitespace is trimmed before comparison', 0, () => {
      expect(isReasoningEchoOfReply('  当前目录是空的。\n', '当前目录是空的。')).assertTrue()
    })
  })
}
