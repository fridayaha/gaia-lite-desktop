import { logI } from '../util/Logger';
import fs from '@ohos.file.fs';
import buffer from '@ohos.buffer';

/**
 * 流式渲染全链路打点 —— 定位"几十秒后内容一下子全出来"类问题。
 * 复刻 Android sse/StreamProbe.kt。ArkTS 端用 @ohos.file.fs 写文件。
 *
 * 三层计数（每秒聚合一行，零值秒也输出——空白段正是定位线索）：
 * - sse  ：SseClient 读到 data 行
 * - vm   ：ViewModel 处理 StreamEvent
 * - cmp  ：组件层拿到新 markdown
 * - rndr ：MarkdownText 节流后实际渲染
 *
 * 里程碑（MARK 行）：POST 开始/拿到 run_id/SSE 开流/首个 delta/工具事件/流结束。
 * 只记元数据，不记消息内容。
 */
export class StreamProbe {
  private static readonly MAX_BYTES = 512 * 1024

  /** 测试注入：虚拟时钟；默认系统单调时钟 */
  static clock: () => number = () => Date.now()

  /** 测试注入：logcat 输出 */
  static logcat: (s: string) => void = (s) => logI('StreamProbe', s)

  /** 测试注入：为 null 时跳过文件写入 */
  static logFilePath: string | null = null

  /** 最近输出的行，单测断言用；上限 2000 行 */
  static recentLines: string[] = []

  private static active = false
  private static t0 = 0
  private static bucketSec = -1
  private static sseCount = 0
  private static sseBytes = 0
  private static vmCount = 0
  private static cmpCount = 0
  private static rndrCount = 0
  private static rndrBytes = 0
  private static totalSse = 0
  private static totalVm = 0
  private static totalCmp = 0
  private static totalRndr = 0

  static init(filePath: string): void {
    StreamProbe.logFilePath = filePath
  }

  /** App 启动头（版本/设备） */
  static appStart(header: string): void {
    StreamProbe.line(`──── APP ${header}`)
  }

  static begin(meta: string): void {
    StreamProbe.t0 = StreamProbe.clock()
    StreamProbe.active = true
    StreamProbe.bucketSec = -1
    StreamProbe.sseCount = 0; StreamProbe.sseBytes = 0
    StreamProbe.vmCount = 0; StreamProbe.cmpCount = 0
    StreamProbe.rndrCount = 0; StreamProbe.rndrBytes = 0
    StreamProbe.totalSse = 0; StreamProbe.totalVm = 0
    StreamProbe.totalCmp = 0; StreamProbe.totalRndr = 0
    StreamProbe.line(`════ BEGIN ${meta}`)
  }

  static mark(phase: string, detail: string = ''): void {
    if (!StreamProbe.active) return
    StreamProbe.flushBucketsUpTo(StreamProbe.elapsedSec())
    StreamProbe.line(`t=+${StreamProbe.elapsedMs()}ms MARK ${phase}${detail.length > 0 ? ' ' + detail : ''}`)
  }

  static tickSse(payloadChars: number): void {
    StreamProbe.tick(1, payloadChars, 0, 0, 0, 0)
  }
  static tickVm(): void { StreamProbe.tick(0, 0, 1, 0, 0, 0) }
  static tickCompose(): void { StreamProbe.tick(0, 0, 0, 1, 0, 0) }
  static tickRender(textChars: number): void { StreamProbe.tick(0, 0, 0, 0, 1, textChars) }

  private static tick(
    sse: number, sseB: number, vm: number, cmp: number, rndr: number, rndrB: number,
  ): void {
    if (!StreamProbe.active) return
    const sec = StreamProbe.elapsedSec()
    StreamProbe.flushBucketsUpTo(sec)
    if (StreamProbe.bucketSec < sec) StreamProbe.bucketSec = sec
    StreamProbe.sseCount += sse
    StreamProbe.sseBytes += sseB
    StreamProbe.vmCount += vm
    StreamProbe.cmpCount += cmp
    StreamProbe.rndrCount += rndr
    StreamProbe.rndrBytes += rndrB
    StreamProbe.totalSse += sse
    StreamProbe.totalVm += vm
    StreamProbe.totalCmp += cmp
    StreamProbe.totalRndr += rndr
  }

  static end(reason: string): void {
    if (!StreamProbe.active) return
    StreamProbe.flushBucketsUpTo(StreamProbe.elapsedSec())
    StreamProbe.flushBucket()
    StreamProbe.line(`════ END ${reason} dur=${StreamProbe.elapsedMs()}ms sse=${StreamProbe.totalSse} vm=${StreamProbe.totalVm} cmp=${StreamProbe.totalCmp} rndr=${StreamProbe.totalRndr}`)
    StreamProbe.active = false
  }

  private static elapsedMs(): number { return StreamProbe.clock() - StreamProbe.t0 }
  private static elapsedSec(): number { return Math.floor(StreamProbe.elapsedMs() / 1000) }

  private static flushBucketsUpTo(sec: number): void {
    if (StreamProbe.bucketSec < 0) {
      StreamProbe.bucketSec = sec - 1
      if (StreamProbe.bucketSec < 0) StreamProbe.bucketSec = 0
    }
    while (StreamProbe.bucketSec < sec) {
      StreamProbe.flushBucket()
      StreamProbe.bucketSec++
    }
  }

  private static flushBucket(): void {
    if (StreamProbe.bucketSec < 0) return
    StreamProbe.line(`t=+${StreamProbe.bucketSec}s sse=${StreamProbe.sseCount}/${StreamProbe.sseBytes}B vm=${StreamProbe.vmCount} cmp=${StreamProbe.cmpCount} rndr=${StreamProbe.rndrCount}/${StreamProbe.rndrBytes}B`)
    StreamProbe.sseCount = 0; StreamProbe.sseBytes = 0
    StreamProbe.vmCount = 0; StreamProbe.cmpCount = 0
    StreamProbe.rndrCount = 0; StreamProbe.rndrBytes = 0
  }

  private static line(s: string): void {
    StreamProbe.logcat(s)
    StreamProbe.recentLines.push(s)
    while (StreamProbe.recentLines.length > 2000) StreamProbe.recentLines.shift()
    const path = StreamProbe.logFilePath
    if (path === null) return
    try {
      // 文件超 MAX_BYTES 截断保留后半
      const f = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE)
      try {
        const stat = fs.statSync(f.fd)
        if (stat.size > StreamProbe.MAX_BYTES) {
          // 简化：直接覆盖重写
          fs.truncateSync(path, 0)
        }
        fs.writeSync(f.fd, `${s}\n`)
      } finally {
        fs.closeSync(f)
      }
    } catch (e) {
      // 文件 IO 失败不影响功能
    }
  }

  static readLog(): string {
    const path = StreamProbe.logFilePath
    if (path === null) return ''
    try {
      const f = fs.openSync(path, fs.OpenMode.READ_ONLY)
      try {
        const stat = fs.statSync(f.fd)
        const buf = new ArrayBuffer(stat.size)
        fs.readSync(f.fd, buf)
        return buffer.from(buf).toString('utf-8')
      } finally {
        fs.closeSync(f)
      }
    } catch (e) {
      return ''
    }
  }

  static clearLog(): void {
    const path = StreamProbe.logFilePath
    if (path !== null) {
      try { fs.unlinkSync(path) } catch (e) {}
    }
    StreamProbe.recentLines = []
  }
}
