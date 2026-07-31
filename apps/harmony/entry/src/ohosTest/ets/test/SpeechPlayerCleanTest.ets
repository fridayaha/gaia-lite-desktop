import { describe, it, expect } from '@ohos/hypium';
import { cleanForSpeech } from '../../../main/ets/repo/SpeechPlayer';

/**
 * cleanForSpeech 单元测试。复刻 Android SpeechPlayerTest.kt 的 cleanForSpeech 分支。
 * 验证 markdown 链接/代码块/标题/列表符号清洗规则。
 */
export function registerSpeechPlayerCleanTests(): void {
  describe('SpeechPlayer.cleanForSpeech', () => {
    it('strips fenced code blocks', 0, () => {
      const input = 'before\n```python\nprint(1)\n```\nafter'
      const result = cleanForSpeech(input)
      expect(result.includes('print')).assertFalse()
      expect(result.includes('代码片段')).assertTrue()
      expect(result.includes('before')).assertTrue()
      expect(result.includes('after')).assertTrue()
    })

    it('strips inline code', 0, () => {
      const input = 'use `print()` function'
      const result = cleanForSpeech(input)
      expect(result.includes('print()')).assertFalse()
    })

    it('keeps link text but drops URL', 0, () => {
      const input = 'see [docs](https://example.com) for details'
      const result = cleanForSpeech(input)
      expect(result.includes('docs')).assertTrue()
      expect(result.includes('https://')).assertFalse()
      expect(result.includes('example.com')).assertFalse()
    })

    it('image becomes alt text', 0, () => {
      const input = '![alt text](image.png)'
      const result = cleanForSpeech(input)
      expect(result.includes('alt text')).assertTrue()
      expect(result.includes('image.png')).assertFalse()
    })

    it('strips ATX headings', 0, () => {
      const input = '## Title\nbody'
      const result = cleanForSpeech(input)
      expect(result.includes('#')).assertFalse()
      expect(result.includes('Title')).assertTrue()
    })

    it('strips bold/italic markers', 0, () => {
      const input = '**bold** and *italic* and __more__'
      const result = cleanForSpeech(input)
      expect(result.includes('*')).assertFalse()
      expect(result.includes('_')).assertFalse()
      expect(result.includes('bold')).assertTrue()
      expect(result.includes('italic')).assertTrue()
      expect(result.includes('more')).assertTrue()
    })

    it('strips blockquote markers', 0, () => {
      const input = '> quoted\ntext'
      const result = cleanForSpeech(input)
      expect(result.includes('>')).assertFalse()
      expect(result.includes('quoted')).assertTrue()
    })

    it('strips unordered list markers', 0, () => {
      const input = '- item one\n* item two\n+ item three'
      const result = cleanForSpeech(input)
      expect(result.includes('item one')).assertTrue()
      expect(result.includes('item two')).assertTrue()
      expect(result.includes('item three')).assertTrue()
    })

    it('collapses repeated whitespace', 0, () => {
      const input = 'a    b\n\n\n  c'
      const result = cleanForSpeech(input)
      expect(result.includes('  ')).assertFalse()
    })

    it('empty input returns empty', 0, () => {
      expect(cleanForSpeech('')).assertEqual('')
    })
  })
}
