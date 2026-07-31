/**
 * 最新值节流（leading + trailing）：窗口内首个值立即发出，窗口期内到达的值只保留最新一个，
 * 在窗口结束时发出。复刻 Android FlowThrottle.kt 语义（Android 用 channelFlow + collectLatest
 * 实现，HarmonyOS 无 Flow，用 setTimeout + 状态机）。
 *
 * 用途：流式渲染场景（SSE delta 高峰 ~76/s）下游若挂重渲染，逐值渲染会打满主线程（O(n²)）。
 * 节流后渲染帧率上限 1000/periodMs。
 *
 * 语义对齐：
 * - 首值 lastEmitAt=null → 立即发，lastEmitAt=now
 * - 窗口内新值：若有 pending timer 直接替换 pendingValue（保持原 firing 时间），否则安排 timer
 * - timer 到点：发 pendingValue，清空，lastEmitAt=now
 * - now - lastEmitAt >= periodMs：直接发（窗口已过），lastEmitAt=now
 */
export class Throttle<T> {
  private lastEmitAt: number | null = null
  private timer: number | null = null
  private pendingValue: T | null = null
  private hasPending: boolean = false
  private readonly periodMs: number
  private readonly onEmit: (v: T) => void
  private readonly now: () => number

  constructor(
    periodMs: number,
    onEmit: (v: T) => void,
    now: () => number = Date.now,
  ) {
    this.periodMs = periodMs
    this.onEmit = onEmit
    this.now = now
  }

  emit(v: T): void {
    const t = this.now()
    if (this.lastEmitAt === null || t - this.lastEmitAt >= this.periodMs) {
      if (this.timer !== null) {
        clearTimeout(this.timer)
        this.timer = null
        this.hasPending = false
      }
      this.lastEmitAt = t
      this.onEmit(v)
      return
    }
    this.pendingValue = v
    this.hasPending = true
    if (this.timer !== null) return
    const wait = this.periodMs - (t - this.lastEmitAt)
    const self = this
    this.timer = setTimeout(() => {
      const emitValue = self.pendingValue
      const hasEmit = self.hasPending
      self.pendingValue = null
      self.hasPending = false
      self.timer = null
      if (!hasEmit) return
      if (emitValue === null) return
      self.lastEmitAt = self.now()
      self.onEmit(emitValue)
    }, wait)
  }

  cancel(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    this.pendingValue = null
    this.hasPending = false
  }

  flush(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.hasPending) {
      const v = this.pendingValue
      this.pendingValue = null
      this.hasPending = false
      this.lastEmitAt = this.now()
      if (v !== null) this.onEmit(v)
    }
  }
}
