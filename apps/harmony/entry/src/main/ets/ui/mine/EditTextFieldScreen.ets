import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';

/**
 * 通用文本字段编辑页。复刻 Android ui/mine/EditTextFieldScreen.kt。
 */

@Component
export struct EditTextFieldScreen {
  @Consume('container') container: AppContainer
  @Prop field: string = ''
  @State state: MineUiState = new MineUiState()
  @State value: string = ''
  @State error: string | null = null
  @State pendingSave: boolean = false
  private vm: MineViewModel | null = null
  private listener: (s: MineUiState) => void = (s) => {
    this.state = s
    if (this.value === '' && s.user !== null) {
      this.value = this.initialValue(s)
    }
    if (this.pendingSave && !s.profileSaving && s.profileError === null) {
      this.pendingSave = false
      this.onBack()
    }
    if (s.profileError !== null && this.pendingSave) {
      this.error = s.profileError
      this.pendingSave = false
      this.vm?.clearProfileError()
    }
  }

  aboutToAppear(): void {
    this.vm = new MineViewModel(
      this.container.authRepository,
      this.container.agentContext,
      this.container.appReleaseRepository,
      this.container.messageFeedbackRepository,
      this.container.developerModeStore,
      this.container.accountStore,
      this.container.hapDownloader,
      this.container.serverConfig,
      this.container.tokenStorage,
    )
    this.vm.subscribe(this.listener)
    this.vm.bootstrap()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  private title(): string {
    if (this.field === 'real_name') return '真实姓名'
    if (this.field === 'email') return '邮箱'
    if (this.field === 'phone') return '手机'
    return '编辑'
  }

  private initialValue(s: MineUiState): string {
    const user = s.user
    if (user === null) return ''
    if (this.field === 'real_name') return user.real_name ?? ''
    if (this.field === 'email') return user.email ?? ''
    if (this.field === 'phone') return user.phone ?? ''
    return ''
  }

  private keyboardType(): InputType {
    if (this.field === 'email') return InputType.Email
    if (this.field === 'phone') return InputType.PhoneNumber
    return InputType.Normal
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  async onSave(): Promise<void> {
    const trimmed = this.value.trim()
    if (trimmed.length === 0) {
      this.error = '不能为空'
      return
    }
    const initial = this.initialValue(this.state).trim()
    if (trimmed === initial) {
      this.onBack()
      return
    }
    this.pendingSave = true
    this.error = null
    if (this.field === 'real_name') {
      await this.vm?.updateProfile(trimmed, null, null)
    } else if (this.field === 'email') {
      await this.vm?.updateProfile(null, trimmed, null)
    } else if (this.field === 'phone') {
      await this.vm?.updateProfile(null, null, trimmed)
    }
  }

  build(): void {
    Column() {
      TopBar(this.title(), () => this.onBack())
      Column() {
        TextInput({ placeholder: this.title(), text: this.value })
          .type(this.keyboardType())
          .width('100%')
          .height(48)
          .onChange((v) => { this.value = v; this.error = null })

        if (this.error !== null) {
          Text(this.error ?? '')
            .fontSize(Typography.labelSmall)
            .fontColor(LightTheme.error)
            .margin({ top: 8 })
            .width('100%')
        }

        Blank().height(12)

        Button(this.state.profileSaving ? '保存中…' : '保存')
          .type(ButtonType.Capsule)
          .width('100%')
          .enabled(!this.state.profileSaving)
          .onClick(() => { this.onSave() })

        if (this.field === 'email' || this.field === 'phone') {
          Text('修改后认证状态会重置，需重新认证。')
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .margin({ top: 8 })
            .width('100%')
        }
      }
      .width('100%')
      .padding(16)
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}
