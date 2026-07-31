import { LoginViewModel } from './LoginViewModel';
import { LoginUiState, LoginTab } from './LoginUiState';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { logI } from '../../util/Logger';

/**
 * 登录页。复刻 Android ui/login/LoginScreen.kt。
 * Phase 0 仅 ACCOUNT tab；其他 tab（EMAIL/PHONE/SMS_CODE）+ 忘记密码留 Phase 1。
 */

@Component
export struct LoginScreen {
  @Consume('container') container: AppContainer
  @State state: LoginUiState = new LoginUiState()
  private vm: LoginViewModel | null = null
  private listener: (s: LoginUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    logI('LoginScreen', 'aboutToAppear')
    this.vm = new LoginViewModel(this.container.authRepository)
    this.vm.subscribe(this.listener)
    this.vm.fetchVerificationChannels()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  async onSubmit(): Promise<void> {
    if (this.vm === null) return
    await this.vm.submit()
    if (this.state.loggedInUser !== null) {
      AppRouter.instance().replace(Routes.AGENT_LIST)
    }
  }

  @Builder
  tabsBar(): void {
    // Phase 0 仅 ACCOUNT tab，后续加 EMAIL/PHONE/SMS_CODE
    Row() {
      Text('账号')
        .fontSize(Typography.bodyLarge)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.accent)
        .padding({ left: 16, right: 16, top: 12, bottom: 12 })
    }
    .width('100%')
    .justifyContent(FlexAlign.Center)
    .margin({ bottom: 24 })
  }

  build(): void {
    Column() {
      Scroll() {
        Column() {
          // 应用名（displayLarge + 主色）
          Text('知行')
            .fontSize(Typography.displayLarge)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.accent)

          Blank().height(32)

          // tabs（Phase 0 仅账号）
          this.tabsBar()

          // 用户名
          TextInput({ placeholder: '用户名', text: this.state.username })
            .type(InputType.Normal)
            .width('100%')
            .height(48)
            .onChange((v) => { this.vm?.onUsernameChange(v) })

          Blank().height(12)

          // 密码
          TextInput({ placeholder: '密码', text: this.state.password })
            .type(InputType.Password)
            .width('100%')
            .height(48)
            .onChange((v) => { this.vm?.onPasswordChange(v) })

          Blank().height(12)

          // 错误提示
          if (this.state.error !== null && this.state.error.length > 0) {
            Text(this.state.error)
              .fontSize(Typography.bodySmall)
              .fontColor(LightTheme.error)
              .margin({ bottom: 8 })
              .width('100%')
          }

          // 成功提示
          if (this.state.success !== null && this.state.success.length > 0) {
            Text(this.state.success)
              .fontSize(Typography.bodySmall)
              .fontColor(LightTheme.accent)
              .margin({ bottom: 8 })
              .width('100%')
          }

          // 登录按钮（占满 + 48 高）
          Button(this.state.loading ? '登录中...' : '登录')
            .type(ButtonType.Capsule)
            .width('100%')
            .height(48)
            .backgroundColor(LightTheme.accent)
            .fontColor(LightTheme.onAccent)
            .enabled(!this.state.loading)
            .onClick(() => { this.onSubmit() })

          Blank().height(12)

          // 忘记密码（Phase 1 接逻辑）
          Text('忘记密码？')
            .fontSize(Typography.bodyMedium)
            .fontColor(LightTheme.muted)
            .margin({ top: 8 })
        }
        .width('100%')
        .padding({ left: 24, right: 24 })
        .alignItems(HorizontalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .constraintSize({ minHeight: '100%' })
      }
      .layoutWeight(1)
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
    .justifyContent(FlexAlign.Center)
  }
}
