import { StreamEvent } from './StreamEvent';

/**
 * SSE payload → StreamEvent 解析。复刻 Android sse/StreamEventParser.kt。
 * 抽成独立 class 便于单测。
 *
 * 两类帧：
 * - 非 Hermes（OpenAI chat-completions）：choices[0].delta.{content, reasoning_content}
 * - Hermes：event 字段路由（run.started | message.delta | reasoning.available | tool.* | approval.* | run.*）
 */
export class StreamEventParser {
  /** 非 Hermes（chat-completions）帧解析。 */
  static parseNonHermes(payload: string): StreamEvent | null {
    let obj: Record<string, Object>
    try {
      obj = JSON.parse(payload) as Record<string, Object>
    } catch (e) {
      return null
    }

    // 工具进度（p.tool + p.status）
    const tool = typeof obj['tool'] === 'string' ? obj['tool'] as string : null
    const status = typeof obj['status'] === 'string' ? obj['status'] as string : null
    if (tool !== null && status !== null) {
      const label = typeof obj['label'] === 'string' ? obj['label'] as string : ''
      const toolCallId = typeof obj['toolCallId'] === 'string' ? obj['toolCallId'] as string : null
      if (status === 'running') {
        return StreamEvent.toolStarted(tool, label, toolCallId)
      }
      if (status === 'completed' || status === 'success') {
        const err = StreamEventParser.parseToolError(obj['error'])
        const result = typeof obj['result'] === 'string' ? obj['result'] as string : null
        return StreamEvent.toolCompleted(tool, err, result)
      }
      return null
    }

    // run 生命周期 + 审批
    const type = typeof obj['type'] === 'string' ? obj['type'] as string : null
    if (type === 'run.start') {
      const runId = typeof obj['run_id'] === 'string' ? obj['run_id'] as string : ''
      if (runId.length === 0) return null
      return StreamEvent.runStarted(runId)
    }
    if (type === 'approval.request') {
      const runId = typeof obj['run_id'] === 'string' ? obj['run_id'] as string : ''
      const command = typeof obj['command'] === 'string' ? obj['command'] as string : ''
      const description = typeof obj['description'] === 'string' ? obj['description'] as string : ''
      const choices = StreamEventParser.parseChoices(obj['choices'])
      return StreamEvent.approvalRequested(runId, command, description, choices)
    }
    if (type === 'approval.responded') {
      const choice = typeof obj['choice'] === 'string' ? obj['choice'] as string : null
      if (choice === null) return null
      return StreamEvent.approvalResponded(choice)
    }

    // 常规 chat-completions 帧
    const choices = obj['choices']
    let delta: Record<string, Object> | null = null
    if (choices instanceof Array && choices.length > 0) {
      const choicesArr = choices as Object[]
      const first = choicesArr[0]
      if (first instanceof Object) {
        const deltaObj = (first as Record<string, Object>)['delta']
        if (deltaObj instanceof Object) {
          delta = deltaObj as Record<string, Object>
        }
      }
    }
    if (delta !== null) {
      const content = typeof delta['content'] === 'string' ? delta['content'] as string : null
      const reasoning = (typeof delta['reasoning_content'] === 'string' ? delta['reasoning_content'] as string : null)
        ?? (typeof delta['reasoning'] === 'string' ? delta['reasoning'] as string : null)
      if (content !== null) return StreamEvent.contentDelta(content)
      if (reasoning !== null) return StreamEvent.reasoningDelta(reasoning)
    }
    const usage = obj['usage']
    if (usage instanceof Object) {
      return StreamEvent.completed(usage)
    }
    return null
  }

  /** Hermes event 帧（payload 含 event 字段）。 */
  static parseHermes(payload: string): StreamEvent | null {
    let obj: Record<string, Object>
    try {
      obj = JSON.parse(payload) as Record<string, Object>
    } catch (e) {
      return null
    }
    const event = typeof obj['event'] === 'string' ? obj['event'] as string : null
    if (event === null) return null
    const str = (k: string): string => (typeof obj[k] === 'string' ? obj[k] as string : '')

    switch (event) {
      case 'run.started':
        return StreamEvent.runStarted(str('run_id'))
      case 'message.delta': {
        const delta = str('delta')
        if (delta.length > 0) return StreamEvent.hermesDelta(delta)
        return null
      }
      case 'reasoning.available': {
        const text = str('text')
        if (text.length > 0) return StreamEvent.hermesReasoning(text)
        return null
      }
      case 'tool.started':
        return StreamEvent.toolStarted(
          str('tool') || 'tool',
          str('preview'),
          typeof obj['toolCallId'] === 'string' ? obj['toolCallId'] as string : null,
        )
      case 'tool.completed':
        return StreamEvent.toolCompleted(
          str('tool') || 'tool',
          StreamEventParser.parseToolError(obj['error']),
          str('result') || str('output') || null,
        )
      case 'approval.request':
        return StreamEvent.approvalRequested(
          str('run_id'),
          str('command'),
          str('description'),
          StreamEventParser.parseChoices(obj['choices']),
        )
      case 'approval.responded': {
        const choice = str('choice')
        if (choice.length === 0) return null
        return StreamEvent.approvalResponded(choice)
      }
      case 'run.completed':
        return StreamEvent.completed(
          obj['usage'] instanceof Object ? obj['usage'] : null,
          str('output') || null,
        )
      case 'run.failed':
        return StreamEvent.failed(str('error') || '运行失败')
      case 'run.cancelled':
        return StreamEvent.cancelled()
      default:
        return null
    }
  }

  private static parseChoices(elem: Object | undefined): string[] {
    const fallback: string[] = ['once', 'session', 'always', 'deny']
    if (!(elem instanceof Array)) return fallback
    const out: string[] = []
    const arr = elem as Object[]
    for (const v of arr) {
      if (typeof v === 'string') out.push(v)
    }
    return out.length > 0 ? out : fallback
  }

  /**
   * 从 SSE tool.completed 事件抽取 error 字段。
   * - Hermes: 发 boolean is_error 标志（true=失败，false=成功），不带错误信息 → 返回占位文案
   * - 非 Hermes: 可能发 string 错误信息 → 原样保留
   * - null / undefined: 成功，返回 null
   */
  private static parseToolError(elem: Object | undefined): string | null {
    if (elem === null || elem === undefined) return null
    if (typeof elem === 'string') return elem
    if (typeof elem === 'boolean') return elem ? '工具执行失败' : null
    return null
  }
}
