import { ApprovalState } from '../ChatUiState';

/**
 * 审批卡片。复刻 Android ui/chat/components/ApprovalCard.kt。
 * 引擎请求用户审批工具调用：显示 command + description + choices（once/session/always/deny）。
 */

@Component
export struct ApprovalCard {
  @Prop state: ApprovalState
  onChoice: (choice: string) => void = () => {}

  build(): void {
    Column() {
      // 命令
      if (this.state.command.length > 0) {
        Text(this.state.command)
          .fontSize(14)
          .fontWeight(FontWeight.Medium)
          .fontColor('#1F2937')
          .margin({ bottom: 4 })
      }
      // 描述
      if (this.state.description.length > 0) {
        Text(this.state.description)
          .fontSize(13)
          .fontColor('#4B5563')
          .margin({ bottom: 8 })
      }
      // 选项按钮
      Row() {
        ForEach(this.state.choices, (c: string, idx: number) => {
          Button(this.buttonLabel(c))
            .type(ButtonType.Capsule)
            .height(32)
            .fontSize(13)
            .backgroundColor(this.buttonColor(c))
            .fontColor('#FFFFFF')
            .enabled(!this.state.submitting && !this.state.responded)
            .margin({ right: 8 })
            .onClick(() => { this.onChoice(c) })
        }, (c: string, idx: number) => `${idx}-${c}`)
      }
      .width('100%')

      if (this.state.responded) {
        Text(`已选择：${this.state.respondedChoice ?? ''}`)
          .fontSize(12)
          .fontColor('#10B981')
          .margin({ top: 6 })
      } else if (this.state.submitting) {
        Text('提交中...')
          .fontSize(12)
          .fontColor('#6B7280')
          .margin({ top: 6 })
      }
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#FEF3C7')
    .borderRadius(8)
    .margin({ bottom: 6 })
  }

  private buttonLabel(choice: string): string {
    switch (choice) {
      case 'once': return '本次'
      case 'session': return '本会话'
      case 'always': return '总是'
      case 'deny': return '拒绝'
      default: return choice
    }
  }

  private buttonColor(choice: string): string {
    return choice === 'deny' ? '#DC2626' : '#2563EB'
  }
}
