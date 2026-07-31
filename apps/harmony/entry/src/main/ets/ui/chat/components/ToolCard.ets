import { ToolCallState } from '../../../net/dto/ChatDtos';
import { IconPaths, MIcon } from '../../theme/Icons';

/**
 * 工具调用卡片。复刻 Android ui/chat/components/ToolCard.kt。
 * 显示工具名 + preview；completed 后展开 result。
 */

@Component
export struct ToolCard {
  @Prop tool: ToolCallState
  @State expanded: boolean = false

  build(): void {
    Column() {
      Row() {
        if (this.tool.completed) {
          MIcon({ path: IconPaths.check, iconSize: 14, color: this.tool.error !== null ? 0xFFDC2626 : 0xFF10B981 })
            .margin({ right: 6 })
        } else {
          MIcon({ path: IconPaths.schedule, iconSize: 14, color: 0xFF6B7280 })
            .margin({ right: 6 })
        }
        Text(this.tool.name)
          .fontSize(13)
          .fontWeight(FontWeight.Medium)
          .fontColor('#1F2937')
          .layoutWeight(1)
        if (this.tool.preview.length > 0) {
          Text(this.tool.preview)
            .fontSize(12)
            .fontColor('#6B7280')
            .maxLines(1)
            .margin({ right: 6 })
        }
        if (this.tool.result !== null && this.tool.result.length > 0) {
          MIcon({
            path: this.expanded ? IconPaths.keyboardArrowDown : IconPaths.keyboardArrowRight,
            iconSize: 16,
            color: 0xFF6B7280,
          })
            .onClick(() => { this.expanded = !this.expanded })
        }
      }
      .width('100%')
      .padding({ left: 8, right: 8, top: 6, bottom: 6 })
      .backgroundColor('#F3F4F6')
      .borderRadius(8)
      .alignItems(VerticalAlign.Center)

      if (this.tool.error !== null) {
        Text(`错误：${this.tool.error}`)
          .fontSize(12)
          .fontColor('#DC2626')
          .padding({ left: 8, right: 8, top: 4 })
      }

      if (this.expanded && this.tool.result !== null) {
        Text(this.tool.result)
          .fontSize(12)
          .fontFamily('monospace')
          .fontColor('#4B5563')
          .backgroundColor('#F9FAFB')
          .borderRadius(6)
          .padding(8)
          .margin({ top: 4 })
      }
    }
    .width('100%')
    .margin({ bottom: 6 })
  }
}
