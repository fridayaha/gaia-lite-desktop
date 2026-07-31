import { IconPaths, MIcon } from '../../theme/Icons';

/**
 * 思考卡片。复刻 Android ui/chat/components/ThinkingCard.kt。
 * 流式 thinkingText 实时展示；点击折叠/展开。
 */

@Component
export struct ThinkingCard {
  @Prop text: string = ''
  @State expanded: boolean = false

  build(): void {
    if (this.text.length === 0) {
      Column() {}
    } else {
      Column() {
        Row() {
          Text('思考过程')
            .fontSize(13)
            .fontColor('#6B7280')
            .fontWeight(FontWeight.Medium)
            .layoutWeight(1)
          MIcon({
            path: this.expanded ? IconPaths.keyboardArrowDown : IconPaths.keyboardArrowRight,
            iconSize: 16,
            color: 0xFF6B7280,
          })
        }
        .width('100%')
        .padding({ left: 8, right: 8, top: 6, bottom: 6 })
        .backgroundColor('#F3F4F6')
        .borderRadius({ topLeft: 8, topRight: 8 })
        .alignItems(VerticalAlign.Center)
        .onClick(() => { this.expanded = !this.expanded })

        if (this.expanded) {
          Text(this.text)
            .fontSize(13)
            .fontColor('#4B5563')
            .padding({ left: 8, right: 8, top: 6, bottom: 6 })
            .backgroundColor('#F9FAFB')
            .borderRadius({ bottomLeft: 8, bottomRight: 8 })
        }
      }
      .width('100%')
      .margin({ bottom: 6 })
    }
  }
}
