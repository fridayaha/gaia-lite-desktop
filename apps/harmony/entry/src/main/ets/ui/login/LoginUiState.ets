import { UserInfo } from '../../net/dto/AuthDtos';
import { VerificationChannelsResponse } from '../../net/dto/AuthDtos';

/**
 * 登录 UI 状态。复刻 Android ui/login/LoginUiState.kt。
 * Phase 0 仅做 ACCOUNT 子 tab；其他 tab（EMAIL/PHONE/SMS_CODE）+ 忘记密码留 Phase 1。
 */
export enum LoginTab {
  ACCOUNT = 'ACCOUNT',
  EMAIL = 'EMAIL',
  PHONE = 'PHONE',
  SMS_CODE = 'SMS_CODE',
}

export enum ForgotStep {
  LOADING = 'LOADING',
  PICK = 'PICK',
  CAPTCHA = 'CAPTCHA',
  CODE = 'CODE',
  RESET = 'RESET',
  NO_CHANNEL = 'NO_CHANNEL',
}

export class LoginUiState {
  tab: LoginTab = LoginTab.ACCOUNT
  smsEnabled: boolean = false
  smsPhone: string = ''
  smsCode: string = ''
  smsCaptchaId: string = ''
  smsCaptchaImage: string = ''
  smsCaptchaAnswer: string = ''
  smsCountdown: number = 0
  smsCodeSent: boolean = false
  username: string = ''
  password: string = ''
  email: string = ''
  emailPassword: string = ''
  phone: string = ''
  phonePassword: string = ''
  forgotMode: boolean = false
  forgotStep: ForgotStep = ForgotStep.LOADING
  forgotChannels: VerificationChannelsResponse | null = null
  forgotChannelChoice: string = ''
  forgotTarget: string = ''
  forgotCaptchaId: string = ''
  forgotCaptchaImage: string = ''
  forgotCaptchaAnswer: string = ''
  forgotCode: string = ''
  forgotTicket: string = ''
  forgotNewPassword: string = ''
  forgotConfirmPassword: string = ''
  loading: boolean = false
  error: string | null = null
  success: string | null = null
  loggedInUser: UserInfo | null = null
}
