import { describe, it, expect } from '@ohos/hypium';
import { closeUnclosedFences } from '../../../main/ets/util/CloseFences';

/**
 * closeUnclosedFences 单元测试。复刻 Android CloseFencesTest.kt。
 */
export function registerCloseFencesTests(): void {
  describe('CloseFences', () => {
    it('closed fence unchanged', 0, () => {
      const src = '```kotlin\nfun main() {}\n```'
      expect(closeUnclosedFences(src)).assertEqual(src)
    })

    it('single unclosed fence appends closer', 0, () => {
      const src = '```kotlin\nfun main()'
      const out = closeUnclosedFences(src)
      expect(out.endsWith('```')).assertTrue()
      expect(out.length).assertEqual(src.length + 4) // \n + ```
    })

    it('two unclosed fences still closes only one (奇数)', 0, () => {
      // 反引号数 /3 = 1 (奇数) → 补一个 ```
      // 注意：closeUnclosedFences 算法是反引号数 /3 取整奇偶判定
      // 3 个反引号 /3 = 1 → 奇数 → 补
      // 6 个反引号 /3 = 2 → 偶数 → 不补（认为已闭合）
      // 9 个反引号 /3 = 3 → 奇数 → 补
      const src = '```'
      const out = closeUnclosedFences(src)
      expect(out.length).assertEqual(src.length + 4) // \n + ```
    })

    it('inline code does not affect fence count', 0, () => {
      // 行内代码 ` 不会被算入 fence（因为反引号数 /3 = 0）
      const src = '使用 `fun` 关键字'
      expect(closeUnclosedFences(src)).assertEqual(src)
    })

    it('no backticks unchanged', 0, () => {
      const src = '普通文本，没有反引号'
      expect(closeUnclosedFences(src)).assertEqual(src)
    })
  })
}
