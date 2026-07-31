import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';

/**
 * 设置页通用组件。复刻 Android ui/mine/SettingsCommons.kt。
 * - TopBar：左侧返回按钮（48x48）+ 标题（titleMedium SemiBold）
 * - SettingsCard：白底 + 20 圆角容器
 * - SettingsRow：可选前缀图标 + 标题 + 可选副标题 + 右箭头，最小高度 64，padding 20/14
 */

@Component
export struct SettingsCard {
  @BuilderParam content: () => void

  build(): void {
    Column() {
      this.content()
    }
    .width('100%')
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
  }
}

@Builder
export function TopBar(title: string, onBack: () => void): void {
  Row() {
    Row() {
      MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
    }
    .width(48)
    .height(48)
    .alignItems(VerticalAlign.Center)
    .justifyContent(FlexAlign.Center)
    .onClick(() => onBack())

    Text(title)
      .fontSize(Typography.titleMedium)
      .fontWeight(FontWeight.Bold)
      .fontColor(LightTheme.text)
      .margin({ left: 4 })
    Blank()
  }
  .width('100%')
  .height(64)
  .padding({ left: 4, right: 12 })
  .alignItems(VerticalAlign.Center)
}

@Builder
export function SettingsRow(
  title: string,
  onClick: () => void,
  trailingText: string = '',
  showDivider: boolean = false,
  titleColor: number = LightTheme.text,
  leadingIcon: string = '',
  leadingIconColor: number = LightTheme.muted,
): void {
  Column() {
    Row() {
      if (leadingIcon.length > 0) {
        MIcon({ path: leadingIcon, iconSize: 22, color: leadingIconColor })
          .margin({ right: 14 })
      }
      Text(title)
        .fontSize(Typography.bodyLarge)
        .fontColor(titleColor)
        .layoutWeight(1)
      if (trailingText.length > 0) {
        Text(trailingText)
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.muted)
          .margin({ right: 8 })
      }
      MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 20, color: LightTheme.muted })
    }
    .width('100%')
    .constraintSize({ minHeight: 64 })
    .padding({ left: 20, right: 20, top: 14, bottom: 14 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => onClick())

    if (showDivider) {
      Divider()
        .color(LightTheme.borderVariant)
        .strokeWidth(0.5)
        .margin({ left: 20 })
    }
  }
}

/**
 * SettingsRow with a Toggle at trailing (no arrow).
 * 用于「开发者模式」等开关行。
 */
@Component
export struct SettingsToggleRow {
  @Prop title: string = ''
  @Prop leadingIcon: string = ''
  @Prop leadingIconColor: number = LightTheme.muted
  @Prop checked: boolean = false
  showDivider: boolean = false
  onToggle: (on: boolean) => void = () => {}

  build(): void {
    Column() {
      Row() {
        if (this.leadingIcon.length > 0) {
          MIcon({ path: this.leadingIcon, iconSize: 22, color: this.leadingIconColor })
            .margin({ right: 14 })
        }
        Text(this.title)
          .fontSize(Typography.bodyLarge)
          .fontColor(LightTheme.text)
          .layoutWeight(1)
        Toggle({ type: ToggleType.Switch, isOn: this.checked })
          .selectedColor(LightTheme.accent)
          .onChange((on: boolean) => { this.onToggle(on) })
      }
      .width('100%')
      .constraintSize({ minHeight: 64 })
      .padding({ left: 20, right: 20, top: 14, bottom: 14 })
      .alignItems(VerticalAlign.Center)

      if (this.showDivider) {
        Divider()
          .color(LightTheme.borderVariant)
          .strokeWidth(0.5)
          .margin({ left: 20 })
      }
    }
  }
}
