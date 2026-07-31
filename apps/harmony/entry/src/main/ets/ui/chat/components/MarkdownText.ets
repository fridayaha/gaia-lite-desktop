import { closeUnclosedFences } from '../../../util/CloseFences';
import { Throttle } from '../../../util/Throttle';

/**
 * 自写 markdown 解析器。复刻 Android ui/chat/components/MarkdownText.kt（基于 Markwon）。
 * Phase 0 实现：标题 / 段落 / 代码块 / 行内代码 / 粗体 / 链接。
 * 流式友好：80ms throttle + closeUnclosedFences。
 *
 * ArkUI 用 Text + Span + ImageSpan 树渲染（无 AndroidView）。
 */

interface InlineToken {
  type: 'text' | 'bold' | 'code' | 'link'
  text: string
  href?: string
}

interface Block {
  type: 'h1' | 'h2' | 'h3' | 'h4' | 'p' | 'code' | 'hr' | 'ul' | 'ol'
  text?: string
  items?: string[]  // ul/ol
  lang?: string  // code
}

class MarkdownParser {
  static parse(src: string): Block[] {
    const blocks: Block[] = []
    const lines = src.split('\n')
    let i = 0
    let codeLang = ''
    let codeBuf: string[] = []
    let inCode = false
    while (i < lines.length) {
      const line = lines[i]
      if (line.startsWith('```')) {
        if (!inCode) {
          inCode = true
          codeLang = line.slice(3).trim()
          codeBuf = []
        } else {
          blocks.push({ type: 'code', text: codeBuf.join('\n'), lang: codeLang })
          inCode = false
          codeLang = ''
          codeBuf = []
        }
        i++
        continue
      }
      if (inCode) {
        codeBuf.push(line)
        i++
        continue
      }
      if (line.startsWith('# ')) {
        blocks.push({ type: 'h1', text: line.slice(2).trim() })
      } else if (line.startsWith('## ')) {
        blocks.push({ type: 'h2', text: line.slice(3).trim() })
      } else if (line.startsWith('### ')) {
        blocks.push({ type: 'h3', text: line.slice(4).trim() })
      } else if (line.startsWith('#### ') || line.startsWith('##### ') || line.startsWith('###### ')) {
        blocks.push({ type: 'h4', text: line.replace(/^#+\s/, '').trim() })
      } else if (line.startsWith('---') || line.startsWith('***')) {
        blocks.push({ type: 'hr' })
      } else if (line.startsWith('- ') || line.startsWith('* ')) {
        // 收集连续 ul
        const items: string[] = []
        while (i < lines.length && (lines[i].startsWith('- ') || lines[i].startsWith('* '))) {
          items.push(lines[i].slice(2).trim())
          i++
        }
        blocks.push({ type: 'ul', items })
        continue
      } else if (/^\d+\.\s/.test(line)) {
        const items: string[] = []
        while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
          items.push(lines[i].replace(/^\d+\.\s/, '').trim())
          i++
        }
        blocks.push({ type: 'ol', items })
        continue
      } else if (line.trim().length === 0) {
        // 空行：分块
      } else {
        blocks.push({ type: 'p', text: line })
      }
      i++
    }
    if (inCode) {
      // 流式未闭合代码块：直接落
      blocks.push({ type: 'code', text: codeBuf.join('\n'), lang: codeLang })
    }
    return blocks
  }

  static parseInline(src: string): InlineToken[] {
    const tokens: InlineToken[] = []
    let i = 0
    let buf = ''
    while (i < src.length) {
      // 粗体 **text**
      if (src[i] === '*' && src[i + 1] === '*') {
        const close = src.indexOf('**', i + 2)
        if (close > 0) {
          if (buf.length > 0) { tokens.push({ type: 'text', text: buf }); buf = '' }
          tokens.push({ type: 'bold', text: src.slice(i + 2, close) })
          i = close + 2
          continue
        }
      }
      // 行内代码 `code`
      if (src[i] === '`') {
        const close = src.indexOf('`', i + 1)
        if (close > 0) {
          if (buf.length > 0) { tokens.push({ type: 'text', text: buf }); buf = '' }
          tokens.push({ type: 'code', text: src.slice(i + 1, close) })
          i = close + 1
          continue
        }
      }
      // 链接 [text](href)
      if (src[i] === '[') {
        const closeBracket = src.indexOf(']', i + 1)
        if (closeBracket > 0 && src[closeBracket + 1] === '(') {
          const closeParen = src.indexOf(')', closeBracket + 2)
          if (closeParen > 0) {
            if (buf.length > 0) { tokens.push({ type: 'text', text: buf }); buf = '' }
            tokens.push({
              type: 'link',
              text: src.slice(i + 1, closeBracket),
              href: src.slice(closeBracket + 2, closeParen),
            })
            i = closeParen + 1
            continue
          }
        }
      }
      buf += src[i]
      i++
    }
    if (buf.length > 0) tokens.push({ type: 'text', text: buf })
    return tokens
  }
}

@Component
export struct MarkdownText {
  @Prop @Watch('onSrcChange') src: string = ''
  @State parsed: Block[] = []
  private throttle: Throttle<Block[]> | null = null

  aboutToAppear(): void {
    const emit = (blocks: Block[]) => {
      this.parsed = blocks
    }
    this.throttle = new Throttle<Block[]>(80, emit)
    this.parsed = MarkdownParser.parse(closeUnclosedFences(this.src))
  }

  aboutToDisappear(): void {
    this.throttle?.cancel()
  }

  // src 每次变化都重新解析（节流到 80ms，避免 SSE delta 高峰 ~76/s 打满主线程）
  onSrcChange(): void {
    const blocks = MarkdownParser.parse(closeUnclosedFences(this.src))
    this.throttle?.emit(blocks)
  }

  build(): void {
    Column() {
      if (this.parsed.length === 0) {
        Text('')
      } else {
        ForEach(this.parsed, (block: Block, idx: number) => {
          this.renderBlock(block)
        }, (block: Block, idx: number) => `${idx}-${block.type}-${block.text ?? ''}`)
      }
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  renderBlock(block: Block): void {
    if (block.type === 'h1') {
      Text(block.text ?? '')
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 8, bottom: 4 })
    } else if (block.type === 'h2') {
      Text(block.text ?? '')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 8, bottom: 4 })
    } else if (block.type === 'h3') {
      Text(block.text ?? '')
        .fontSize(16)
        .fontWeight(FontWeight.Medium)
        .margin({ top: 6, bottom: 2 })
    } else if (block.type === 'h4') {
      Text(block.text ?? '')
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 6, bottom: 2 })
    } else if (block.type === 'p') {
      Text() {
        ForEach(MarkdownParser.parseInline(block.text ?? ''), (t: InlineToken, idx: number) => {
          this.renderInline(t)
        }, (t: InlineToken, idx: number) => `${idx}-${t.type}`)
      }
      .margin({ top: 4, bottom: 4 })
    } else if (block.type === 'code') {
      Text(block.text ?? '')
        .fontSize(13)
        .fontFamily('monospace')
        .backgroundColor('#F3F4F6')
        .borderRadius(6)
        .padding(12)
        .width('100%')
        .margin({ top: 4, bottom: 4 })
    } else if (block.type === 'hr') {
      Divider()
        .color('#E5E7EB')
        .margin({ top: 8, bottom: 8 })
    } else if (block.type === 'ul') {
      Column() {
        ForEach(block.items ?? [], (item: string, idx: number) => {
          Row() {
            Text('•')
              .margin({ right: 8 })
            Text() {
              ForEach(MarkdownParser.parseInline(item), (t: InlineToken, i: number) => {
                this.renderInline(t)
              }, (t: InlineToken, i: number) => `${i}-${t.type}`)
            }
            .layoutWeight(1)
          }
          .margin({ bottom: 2 })
        }, (item: string, idx: number) => `${idx}`)
      }
      .alignItems(HorizontalAlign.Start)
    } else if (block.type === 'ol') {
      Column() {
        ForEach(block.items ?? [], (item: string, idx: number) => {
          Row() {
            Text(`${idx + 1}.`)
              .margin({ right: 8 })
            Text() {
              ForEach(MarkdownParser.parseInline(item), (t: InlineToken, i: number) => {
                this.renderInline(t)
              }, (t: InlineToken, i: number) => `${i}-${t.type}`)
            }
            .layoutWeight(1)
          }
          .margin({ bottom: 2 })
        }, (item: string, idx: number) => `${idx}`)
      }
      .alignItems(HorizontalAlign.Start)
    }
  }

  @Builder
  renderInline(t: InlineToken): void {
    if (t.type === 'text') {
      Span(t.text)
    } else if (t.type === 'bold') {
      Span(t.text).fontWeight(FontWeight.Bold)
    } else if (t.type === 'code') {
      Span(t.text).fontFamily('monospace').backgroundColor('#F3F4F6')
    } else if (t.type === 'link') {
      Span(t.text).fontColor('#2563EB').decoration({ type: TextDecorationType.Underline, color: '#2563EB' })
    }
  }
}
