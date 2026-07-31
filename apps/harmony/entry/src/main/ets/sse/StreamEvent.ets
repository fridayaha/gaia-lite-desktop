/**
 * SSE 流事件 union。复刻 Android sse/StreamEvent.kt sealed interface。
 * ArkTS 无 sealed class，用 tagged union（kind 字段）+ 各子类实现。
 *
 * 事件类别：
 * - Connected：流已建立（前端可移除 loading）
 * - ContentDelta / ReasoningDelta：非 Hermes 增量
 * - HermesDelta / HermesReasoning：Hermes 增量
 * - RunStarted / ToolStarted / ToolCompleted / ApprovalRequested / ApprovalResponded
 * - Completed / Failed / Cancelled
 */

export type StreamEventKind =
  | 'Connected'
  | 'ContentDelta'
  | 'ReasoningDelta'
  | 'HermesDelta'
  | 'HermesReasoning'
  | 'RunStarted'
  | 'ToolStarted'
  | 'ToolCompleted'
  | 'ApprovalRequested'
  | 'ApprovalResponded'
  | 'Completed'
  | 'Failed'
  | 'Cancelled'

export class StreamEvent {
  kind: StreamEventKind = 'Connected'
  text: string = ''
  runId: string = ''
  toolName: string = ''
  preview: string = ''
  toolCallId: string | null = null
  error: string | null = null
  result: string | null = null
  command: string = ''
  description: string = ''
  choices: string[] = []
  choice: string = ''
  usage: Object | null = null
  output: string | null = null

  constructor(
    kind: StreamEventKind,
    text: string = '',
    runId: string = '',
    toolName: string = '',
    preview: string = '',
    toolCallId: string | null = null,
    error: string | null = null,
    result: string | null = null,
    command: string = '',
    description: string = '',
    choices: string[] = [],
    choice: string = '',
    usage: Object | null = null,
    output: string | null = null,
  ) {
    this.kind = kind
    this.text = text
    this.runId = runId
    this.toolName = toolName
    this.preview = preview
    this.toolCallId = toolCallId
    this.error = error
    this.result = result
    this.command = command
    this.description = description
    this.choices = choices
    this.choice = choice
    this.usage = usage
    this.output = output
  }

  static connected(): StreamEvent {
    return new StreamEvent('Connected')
  }
  static contentDelta(text: string): StreamEvent {
    return new StreamEvent('ContentDelta', text)
  }
  static reasoningDelta(text: string): StreamEvent {
    return new StreamEvent('ReasoningDelta', text)
  }
  static hermesDelta(text: string): StreamEvent {
    return new StreamEvent('HermesDelta', text)
  }
  static hermesReasoning(text: string): StreamEvent {
    return new StreamEvent('HermesReasoning', text)
  }
  static runStarted(runId: string): StreamEvent {
    const e = new StreamEvent('RunStarted')
    e.runId = runId
    return e
  }
  static toolStarted(name: string, preview: string, toolCallId: string | null): StreamEvent {
    const e = new StreamEvent('ToolStarted')
    e.toolName = name
    e.preview = preview
    e.toolCallId = toolCallId
    return e
  }
  static toolCompleted(name: string, error: string | null, result: string | null): StreamEvent {
    const e = new StreamEvent('ToolCompleted')
    e.toolName = name
    e.error = error
    e.result = result
    return e
  }
  static approvalRequested(runId: string, command: string, description: string, choices: string[]): StreamEvent {
    const e = new StreamEvent('ApprovalRequested')
    e.runId = runId
    e.command = command
    e.description = description
    e.choices = choices
    return e
  }
  static approvalResponded(choice: string): StreamEvent {
    const e = new StreamEvent('ApprovalResponded')
    e.choice = choice
    return e
  }
  static completed(usage: Object | null = null, output: string | null = null): StreamEvent {
    const e = new StreamEvent('Completed')
    e.usage = usage
    e.output = output
    return e
  }
  static failed(error: string): StreamEvent {
    const e = new StreamEvent('Failed')
    e.error = error
    return e
  }
  static cancelled(): StreamEvent {
    return new StreamEvent('Cancelled')
  }
}
