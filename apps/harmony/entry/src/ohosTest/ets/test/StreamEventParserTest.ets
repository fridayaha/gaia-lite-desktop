import { describe, it, expect } from '@ohos/hypium';
import { StreamEventParser } from '../../../main/ets/sse/StreamEventParser';
import { StreamEvent } from '../../../main/ets/sse/StreamEvent';

/**
 * StreamEventParser 单元测试。复刻 Android StreamEventParserTest.kt。
 */
export function registerStreamEventParserTests(): void {
  describe('StreamEventParser_NonHermes', () => {
    it('parses content delta', 0, () => {
      const payload = '{"choices":[{"delta":{"content":"hello"}}]}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ContentDelta')
      expect(ev?.text).assertEqual('hello')
    })

    it('parses reasoning_content delta', 0, () => {
      const payload = '{"choices":[{"delta":{"reasoning_content":"thinking..."}}]}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ReasoningDelta')
      expect(ev?.text).assertEqual('thinking...')
    })

    it('parses reasoning field as fallback', 0, () => {
      const payload = '{"choices":[{"delta":{"reasoning":"alt reasoning"}}]}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ReasoningDelta')
      expect(ev?.text).assertEqual('alt reasoning')
    })

    it('parses usage as completed', 0, () => {
      const payload = '{"usage":{"prompt_tokens":10,"completion_tokens":5}}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('Completed')
    })

    it('parses tool running event', 0, () => {
      const payload = '{"tool":"read_file","status":"running","label":"读取 README","toolCallId":"call-1"}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ToolStarted')
      expect(ev?.toolName).assertEqual('read_file')
      expect(ev?.preview).assertEqual('读取 README')
      expect(ev?.toolCallId).assertEqual('call-1')
    })

    it('parses tool completed with boolean true error', 0, () => {
      const payload = '{"tool":"read_file","status":"completed","error":true}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ToolCompleted')
      expect(ev?.error).assertEqual('工具执行失败')
    })

    it('parses tool completed with string error', 0, () => {
      const payload = '{"tool":"read_file","status":"completed","error":"file not found"}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ToolCompleted')
      expect(ev?.error).assertEqual('file not found')
    })

    it('parses run.start event', 0, () => {
      const payload = '{"type":"run.start","run_id":"r-123"}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('RunStarted')
      expect(ev?.runId).assertEqual('r-123')
    })

    it('parses approval.request event', 0, () => {
      const payload = '{"type":"approval.request","run_id":"r-1","command":"run ls","description":"list files","choices":["once","session"]}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ApprovalRequested')
      expect(ev?.runId).assertEqual('r-1')
      expect(ev?.command).assertEqual('run ls')
      expect(ev?.choices.length).assertEqual(2)
    })

    it('parses approval.responded event', 0, () => {
      const payload = '{"type":"approval.responded","choice":"once"}'
      const ev = StreamEventParser.parseNonHermes(payload)
      expect(ev?.kind).assertEqual('ApprovalResponded')
      expect(ev?.choice).assertEqual('once')
    })

    it('returns null for invalid json', 0, () => {
      expect(StreamEventParser.parseNonHermes('not json')).assertEqual(null)
    })

    it('returns null for unknown shape', 0, () => {
      expect(StreamEventParser.parseNonHermes('{"foo":"bar"}')).assertEqual(null)
    })
  })

  describe('StreamEventParser_Hermes', () => {
    it('parses run.started', 0, () => {
      const payload = '{"event":"run.started","run_id":"r-1"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('RunStarted')
      expect(ev?.runId).assertEqual('r-1')
    })

    it('parses message.delta', 0, () => {
      const payload = '{"event":"message.delta","delta":"hello"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('HermesDelta')
      expect(ev?.text).assertEqual('hello')
    })

    it('parses reasoning.available', 0, () => {
      const payload = '{"event":"reasoning.available","text":"thinking..."}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('HermesReasoning')
      expect(ev?.text).assertEqual('thinking...')
    })

    it('parses tool.started', 0, () => {
      const payload = '{"event":"tool.started","tool":"terminal","preview":"ls -la","toolCallId":"call-1"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('ToolStarted')
      expect(ev?.toolName).assertEqual('terminal')
      expect(ev?.preview).assertEqual('ls -la')
      expect(ev?.toolCallId).assertEqual('call-1')
    })

    it('parses tool.completed with result', 0, () => {
      const payload = '{"event":"tool.completed","tool":"terminal","result":"output"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('ToolCompleted')
      expect(ev?.toolName).assertEqual('terminal')
      expect(ev?.result).assertEqual('output')
    })

    it('parses tool.completed with boolean true error', 0, () => {
      const payload = '{"event":"tool.completed","tool":"terminal","error":true}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('ToolCompleted')
      expect(ev?.error).assertEqual('工具执行失败')
    })

    it('parses approval.request with fallback choices', 0, () => {
      const payload = '{"event":"approval.request","run_id":"r-1","command":"run","description":"test"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('ApprovalRequested')
      expect(ev?.choices.length).assertEqual(4)
      expect(ev?.choices[0]).assertEqual('once')
    })

    it('parses run.completed with usage', 0, () => {
      const payload = '{"event":"run.completed","output":"done","usage":{"tokens":5}}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('Completed')
      expect(ev?.output).assertEqual('done')
    })

    it('parses run.failed', 0, () => {
      const payload = '{"event":"run.failed","error":"timeout"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('Failed')
      expect(ev?.error).assertEqual('timeout')
    })

    it('parses run.cancelled', 0, () => {
      const payload = '{"event":"run.cancelled"}'
      const ev = StreamEventParser.parseHermes(payload)
      expect(ev?.kind).assertEqual('Cancelled')
    })

    it('returns null for unknown event', 0, () => {
      expect(StreamEventParser.parseHermes('{"event":"unknown.event"}')).assertEqual(null)
    })

    it('returns null for missing event field', 0, () => {
      expect(StreamEventParser.parseHermes('{"foo":"bar"}')).assertEqual(null)
    })
  })
}
