import { AuthRepository } from '../../repo/AuthRepository';
import { LoginUiState, LoginTab, ForgotStep } from './LoginUiState';
import { ApiError } from '../../net/ManagerApi';

/**
 * 登录 ViewModel。复刻 Android ui/login/LoginViewModel.kt 的核心 login() 路径。
 * Phase 0 仅 ACCOUNT tab；其他 tab + 忘记密码留 Phase 1。
 *
 * ArkTS 无 ViewModel + viewModelScope，状态由 @State 持有，方法直接 async。
 */
export class LoginViewModel {
  private _state: LoginUiState = new LoginUiState()
  private listeners: Array<(s: LoginUiState) => void> = []
  private authRepo: AuthRepository

  constructor(authRepo: AuthRepository) {
    this.authRepo = authRepo
  }

  get state(): LoginUiState {
    return this._state
  }

  subscribe(listener: (s: LoginUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: LoginUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: LoginUiState) => void): void {
    const next = this.cloneState()
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) {}
    }
  }

  private cloneState(): LoginUiState {
    const s = new LoginUiState()
    s.tab = this._state.tab
    s.smsEnabled = this._state.smsEnabled
    s.smsPhone = this._state.smsPhone
    s.smsCode = this._state.smsCode
    s.smsCaptchaId = this._state.smsCaptchaId
    s.smsCaptchaImage = this._state.smsCaptchaImage
    s.smsCaptchaAnswer = this._state.smsCaptchaAnswer
    s.smsCountdown = this._state.smsCountdown
    s.smsCodeSent = this._state.smsCodeSent
    s.username = this._state.username
    s.password = this._state.password
    s.email = this._state.email
    s.emailPassword = this._state.emailPassword
    s.phone = this._state.phone
    s.phonePassword = this._state.phonePassword
    s.forgotMode = this._state.forgotMode
    s.forgotStep = this._state.forgotStep
    s.forgotChannels = this._state.forgotChannels
    s.forgotChannelChoice = this._state.forgotChannelChoice
    s.forgotTarget = this._state.forgotTarget
    s.forgotCaptchaId = this._state.forgotCaptchaId
    s.forgotCaptchaImage = this._state.forgotCaptchaImage
    s.forgotCaptchaAnswer = this._state.forgotCaptchaAnswer
    s.forgotCode = this._state.forgotCode
    s.forgotTicket = this._state.forgotTicket
    s.forgotNewPassword = this._state.forgotNewPassword
    s.forgotConfirmPassword = this._state.forgotConfirmPassword
    s.loading = this._state.loading
    s.error = this._state.error
    s.success = this._state.success
    s.loggedInUser = this._state.loggedInUser
    return s
  }

  async fetchVerificationChannels(): Promise<void> {
    try {
      const ch = await this.authRepo.getVerificationChannels()
      this.setState((s) => { s.smsEnabled = ch.sms })
    } catch (e) {
      // 失败时默认 smsEnabled=false（隐藏 SMS tab）
    }
  }

  onUsernameChange(v: string): void {
    this.setState((s) => { s.username = v; s.error = null })
  }

  onPasswordChange(v: string): void {
    this.setState((s) => { s.password = v; s.error = null })
  }

  clearError(): void {
    this.setState((s) => { s.error = null })
  }

  /** 提交登录。Phase 0 仅 ACCOUNT tab 实现。 */
  async submit(): Promise<void> {
    if (this._state.loading) return
    const u = this._state.username.trim()
    const p = this._state.password
    if (u.length === 0 || p.length === 0) {
      this.setState((s) => { s.error = '请输入用户名和密码' })
      return
    }
    this.setState((s) => { s.loading = true; s.error = null })
    try {
      const user = await this.authRepo.login(u, p)
      this.setState((s) => { s.loading = false; s.loggedInUser = user })
    } catch (e) {
      const msg = friendlyError(e)
      this.setState((s) => { s.loading = false; s.error = msg })
    }
  }
}

export function friendlyError(e: Object): string {
  if (e instanceof ApiError) {
    // 解析 detail 字段
    try {
      const parsed = JSON.parse(e.body) as Record<string, Object>
      const detail = typeof parsed['detail'] === 'string' ? parsed['detail'] as string : null
      if (detail !== null) return errorMessageFromDetail(detail)
    } catch (err) {
      // JSON 解析失败 → 走通用错误
    }
    if (e.status === 401) return '用户名或密码错误'
    return `请求失败（${e.status}）`
  }
  if (e instanceof Error) return e.message || '网络异常，请稍后重试'
  return '网络异常，请稍后重试'
}

export function errorMessageFromDetail(detail: string): string {
  switch (detail) {
    case 'invalid_credentials': return '用户名或密码错误'
    case 'captcha_required': return '登录失败次数过多，请稍后再试或联系管理员重置'
    case 'captcha_invalid': return '图形验证码错误或已过期'
    case 'account_locked': return '账号已被锁定，请稍后再试'
    case 'ticket_invalid': return '验证码已失效，请重新获取'
    case 'code_invalid': return '验证码错误或已过期'
    case 'password_too_weak': return '密码强度不足，建议 8 位以上含字母与数字'
    default: return '请求失败，请重试'
  }
}
