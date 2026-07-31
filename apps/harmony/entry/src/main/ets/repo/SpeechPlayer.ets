import { textToSpeech } from '@kit.CoreSpeechKit';
import { logI } from '../util/Logger';

const TAG = 'SpeechPlayer'
/** 单次朗读文本上限（引擎对超长文本报 1002300001）。 */
const MAX_SPEAK_CHARS = 3000

/** 引擎就绪前排队的朗读请求（只保留最新一条）。 */
export interface PendingSpeak {
  ref: string
  text: string
}

/**
 * 端侧 TTS 播放。复刻 Android SpeechPlayer：
 * - 懒初始化 textToSpeech 引擎（zh-CN），首次 speak 时创建
 * - 引擎未就绪的请求进 pending 队列，就绪后补播
 * - utterance 完成/停止/出错 → 清 speakingRef（驱动 UI 高亮）
 * - 引擎创建失败 → onUnavailable 回调（UI toast）
 */
export class SpeechPlayer {
  private speakingRef: string | null = null
  /** 当前 utterance 的引擎侧 requestId（speakingRef 清洗后的形态，回调比对用）。 */
  private currentReqId: string | null = null
  private listeners: Array<(ref: string | null) => void> = []
  private engine: textToSpeech.TextToSpeechEngine | null = null
  private initStarted: boolean = false
  private failed: boolean = false
  private pending: PendingSpeak | null = null
  onUnavailable: (() => void) | null = null

  constructor() {}

  toggle(ref: string, text: string): void {
    if (this.speakingRef === ref) {
      this.stop()
      return
    }
    this.speak(ref, text)
  }

  speak(ref: string, text: string): void {
    let cleaned = cleanForSpeech(text)
    if (cleaned.length === 0) return
    if (cleaned.length > MAX_SPEAK_CHARS) {
      cleaned = cleaned.substring(0, MAX_SPEAK_CHARS)
    }
    this.stopCurrent()
    this.setSpeakingRef(ref)
    // 引擎 requestId 只接受字母数字且要求全局唯一（重复/含冒号都会报 401 param error），
    // ref 清洗后拼时间戳 nonce 保证每次 speak 不同
    this.currentReqId = ref.replace(/[^A-Za-z0-9]/g, '') + Date.now().toString(36)
    const engine = this.engine
    if (engine !== null) {
      try {
        engine.speak(cleaned, { requestId: this.currentReqId })
      } catch (e) {
        logI(TAG, `speak invoke failed: ${e instanceof Error ? e.message : String(e)}`)
        this.setSpeakingRef(null)
      }
      return
    }
    if (this.failed) {
      this.setSpeakingRef(null)
      if (this.onUnavailable !== null) this.onUnavailable()
      return
    }
    const p: PendingSpeak = { ref, text: cleaned }
    this.pending = p
    this.ensureEngine()
  }

  stop(): void {
    this.pending = null
    this.stopCurrent()
    this.setSpeakingRef(null)
  }

  get currentSpeakingRef(): string | null {
    return this.speakingRef
  }

  subscribe(listener: (ref: string | null) => void): void {
    this.listeners.push(listener)
    listener(this.speakingRef)
  }

  unsubscribe(listener: (ref: string | null) => void): void {
    this.listeners = this.listeners.filter((l: (ref: string | null) => void) => l !== listener)
  }

  private stopCurrent(): void {
    const engine = this.engine
    if (engine !== null) {
      try {
        if (engine.isBusy()) engine.stop()
      } catch (e) {}
    }
  }

  private ensureEngine(): void {
    if (this.initStarted) return
    this.initStarted = true
    textToSpeech.createEngine({ language: 'zh-CN', person: 0, online: 1 })
      .then((engine: textToSpeech.TextToSpeechEngine) => {
        this.engine = engine
        engine.setListener({
          onStart: (_requestId: string, _response: textToSpeech.StartResponse): void => {},
          onComplete: (requestId: string, _response: textToSpeech.CompleteResponse): void => {
            if (this.currentReqId === requestId) this.setSpeakingRef(null)
          },
          onStop: (requestId: string, _response: textToSpeech.StopResponse): void => {
            if (this.currentReqId === requestId) this.setSpeakingRef(null)
          },
          onError: (requestId: string, errorCode: number, errorMessage: string): void => {
            logI(TAG, `speak error: ${errorCode} ${errorMessage}`)
            if (this.currentReqId === requestId) this.setSpeakingRef(null)
            if (this.onUnavailable !== null) this.onUnavailable()
          },
        })
        logI(TAG, 'engine ready')
        const p = this.pending
        this.pending = null
        if (p !== null && this.speakingRef === p.ref && this.currentReqId !== null) {
          engine.speak(p.text, { requestId: this.currentReqId })
        }
      })
      .catch((e: Error) => {
        logI(TAG, `engine create failed: ${e instanceof Error ? e.message : String(e)}`)
        this.failed = true
        this.pending = null
        this.setSpeakingRef(null)
        if (this.onUnavailable !== null) this.onUnavailable()
      })
  }

  private setSpeakingRef(ref: string | null): void {
    if (this.speakingRef === ref) return
    this.speakingRef = ref
    for (const l of this.listeners) {
      try { l(ref) } catch (e) {}
    }
  }
}

/**
 * 朗读前清洗 markdown/链接/代码块。复刻 Android SpeechPlayer.cleanForSpeech。
 */
export function cleanForSpeech(raw: string): string {
  let s = raw
  // 代码块整体替换
  s = s.replace(/```[\s\S]*?```/g, '，代码片段，')
  s = s.replace(/`[^`]*`/g, ' ')
  // 链接/图片：保留可见文本
  s = s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
  // 标题/加粗/斜体/删除线/引用符号
  s = s.replace(/^\s{0,3}#{1,6}\s*/gm, '')
  s = s.replace(/\*\*|__|\*|~~/g, '')
  s = s.replace(/^\s{0,3}>\s?/gm, '')
  // 无序列表符号
  s = s.replace(/^\s*[-*+]\s+/gm, '')
  // 连续空白
  s = s.replace(/\s{2,}/g, ' ')
  return s.trim()
}
