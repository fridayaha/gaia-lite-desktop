import { AppContainer } from '../../di/AppContainer';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { WorkspaceFileContent } from '../../net/dto/WorkspaceDtos';
import { MarkdownText } from '../chat/components/MarkdownText';
import buffer from '@ohos.buffer';
import image from '@ohos.multimedia.image';
import fs from '@ohos.file.fs';
import { logE } from '../../util/Logger';

/** PixelMap 类型别名 —— @ohos.multimedia.image 模块默认导出为 namespace。 */
type PixelMap = image.PixelMap

type ImageSource = string | PixelMap | Resource;

/**
 * 文件预览屏。复刻 Android ui/workspace/FilePreviewScreen.kt 的核心结构。
 *
 * 支持：
 * - 图片（base64 解码后显示）
 * - Markdown（走 MarkdownText 自写解析器）
 * - 文本（直接 Text）
 * - PDF（用 @ohos.pdf 渲染每页为 PixelMap）
 *
 * Phase 1 子集：不支持 HTML 预览 / Office 外部打开 / 截断提示。
 */

enum PreviewType {
  Image,
  Markdown,
  Text,
  Pdf,
  Unknown,
}

@Component
export struct FilePreviewScreen {
  @Consume('container') container: AppContainer
  @Prop agentId: string = ''
  @Prop path: string = ''
  @State content: WorkspaceFileContent | null = null
  @State rawBytes: ArrayBuffer | null = null
  @State loading: boolean = true
  @State downloadingBinary: boolean = false
  @State error: string | null = null
  @State pdfPageCount: number = 0
  @State pdfCurrentPage: number = 0
  @State pdfPixelMaps: PixelMap[] = []

  async aboutToAppear(): Promise<void> {
    const aid: string = this.agentId
    const p: string = this.path

    this.loading = true
    this.error = null
    this.content = null
    this.rawBytes = null
    try {
      const c = await this.container.workspaceRepository.readFile(aid, p)
      this.content = c
      const type = this.previewType(c)
      if (type === PreviewType.Pdf) {
        this.downloadingBinary = true
        const bytes = await this.container.workspaceRepository.downloadFile(aid, p)
        this.downloadingBinary = false
        if (bytes !== null) {
          this.rawBytes = bytes
          await this.renderPdf(bytes)
        }
      }
    } catch (e) {
      this.error = e instanceof Error ? (e.message || '加载失败') : '加载失败'
    } finally {
      this.loading = false
      this.downloadingBinary = false
    }
  }

  aboutToDisappear(): void {
    for (const pm of this.pdfPixelMaps) {
      try { pm.release() } catch (e) {}
    }
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  private previewType(c: WorkspaceFileContent): PreviewType {
    if (c.is_image) return PreviewType.Image
    if (c.is_markdown) return PreviewType.Markdown
    const ext = fileExtension(c.name).toLowerCase()
    if (ext === 'md' || ext === 'markdown' || ext === 'mkd') return PreviewType.Markdown
    if (ext === 'pdf') return PreviewType.Pdf
    if (c.is_text || c.content !== null || c.content_b64 !== null) return PreviewType.Text
    return PreviewType.Unknown
  }

  /**
   * 把 PDF 字节渲染成 PixelMap 列表。复刻 Android PdfRenderer 用法。
   * 当前 SDK 缺少 @ohos.pdf 模块（PDF 渲染能力），Phase 2 简化为占位：仅留接口签名，
   * 真正渲染依赖 SDK 提供模块后补齐。
   */
  private async renderPdf(bytes: ArrayBuffer): Promise<void> {
    logE('FilePreviewScreen', 'PDF rendering requires @ohos.pdf module (not available in current SDK)')
    this.error = 'PDF 预览暂不可用'
  }

  @Builder
  body(): void {
    if (this.loading) {
      Column() {
        Text('加载中...')
          .fontColor(LightTheme.muted)
      }
      .width('100%')
      .height('100%')
      .justifyContent(FlexAlign.Center)
    } else if (this.error !== null) {
      Column() {
        Text('预览失败')
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
        Text(this.error ?? '')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.muted)
          .margin({ top: 4 })
          .textAlign(TextAlign.Center)
      }
      .width('100%')
      .height('100%')
      .justifyContent(FlexAlign.Center)
      .padding({ left: 24, right: 24 })
    } else if (this.content !== null) {
      this.previewBody()
    }
  }

  @Builder
  previewBody(): void {
    if (this.content === null) {
      Column() {
        Text('无内容')
          .fontColor(LightTheme.muted)
      }
      .width('100%')
      .height('100%')
      .justifyContent(FlexAlign.Center)
    } else if (this.previewType(this.content) === PreviewType.Image) {
      Column() {
        Text('图片预览（base64 解码后通过 PixelMap 显示）')
          .fontColor(LightTheme.muted)
          .fontSize(Typography.bodyMedium)
      }
      .width('100%')
      .height('100%')
      .padding(8)
      .justifyContent(FlexAlign.Center)
    } else if (this.previewType(this.content) === PreviewType.Markdown) {
      Scroll() {
        Column() {
          MarkdownText({ src: this.decodeText(this.content as WorkspaceFileContent) })
        }
        .width('100%')
        .padding(12)
        // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
        .constraintSize({ minHeight: '100%' })
      }
      .width('100%')
      .height('100%')
      .scrollBar(BarState.Auto)
    } else if (this.previewType(this.content) === PreviewType.Text) {
      Scroll() {
        Text(this.decodeText(this.content as WorkspaceFileContent))
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.text)
          .width('100%')
          .padding(12)
          // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
          .constraintSize({ minHeight: '100%' })
      }
      .width('100%')
      .height('100%')
      .scrollBar(BarState.Auto)
    } else if (this.previewType(this.content) === PreviewType.Pdf) {
      if (this.downloadingBinary) {
        Column() {
          Text('PDF 下载中...')
            .fontColor(LightTheme.muted)
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
      } else if (this.pdfPixelMaps.length === 0) {
        Column() {
          Text('无法预览此 PDF')
            .fontColor(LightTheme.muted)
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
      } else {
        Scroll() {
          Column() {
            ForEach(this.pdfPixelMaps, (pm: PixelMap, idx: number) => {
              Image(pm)
                .width('100%')
                .objectFit(ImageFit.Contain)
                .margin({ bottom: 8 })
            }, (pm: PixelMap, idx: number) => `page-${idx}`)
          }
          .width('100%')
          .padding(12)
          // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
          .constraintSize({ minHeight: '100%' })
        }
        .width('100%')
        .height('100%')
        .scrollBar(BarState.Auto)
      }
    } else {
      Column() {
        Text('暂不支持预览此文件类型')
          .fontColor(LightTheme.muted)
      }
      .width('100%')
      .height('100%')
      .justifyContent(FlexAlign.Center)
    }
  }

  private decodeBase64Image(c: WorkspaceFileContent): ArrayBuffer {
    const data = c.content_b64 ?? c.content ?? ''
    if (data.length === 0) return new ArrayBuffer(0)
    try {
      return buffer.from(data, 'base64').buffer
    } catch (e) {
      return new ArrayBuffer(0)
    }
  }

  private decodeText(c: WorkspaceFileContent): string {
    if (c.content !== null) return c.content
    if (c.content_b64 !== null && c.content_b64.length > 0) {
      try {
        return buffer.from(c.content_b64, 'base64').toString('utf-8')
      } catch (e) {
        return ''
      }
    }
    return ''
  }

  build(): void {
    Column() {
      // 顶部标题栏
      Row() {
        Row() {
          MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.onBack() })
        Text(this.content?.name ?? this.path.split('/').pop() ?? '')
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Blank()
      }
      .width('100%')
      .height(56)
      .padding({ left: 4, right: 12 })
      .alignItems(VerticalAlign.Center)

      // 内容区
      Column() {
        this.body()
      }
      .width('100%')
      .layoutWeight(1)
      .backgroundColor(LightTheme.bg)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}

function fileExtension(name: string): string {
  const dotIdx = name.lastIndexOf('.')
  if (dotIdx < 0 || dotIdx === name.length - 1) return ''
  return name.slice(dotIdx + 1)
}
