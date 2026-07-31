import { ManagerApi } from '../net/ManagerApi';
import { TokenStorage, TokenData } from '../data/TokenStorage';
import { LoginRequest, LoginByContactRequest, LoginBySmsCodeRequest, ResetPasswordRequest, UserSelfUpdateRequest, VerificationCodeSendRequest, VerificationCodeVerifyRequest, UserInfo, CaptchaResponse, VerificationChannelsResponse, VerificationCodeSendResponse, VerificationCodeVerifyResponse, ChangeContactRequest, UserVerifyCodeRequest, PresetAvatarsResponse, AvatarUploadResponse } from '../net/dto/AuthDtos';

/**
 * 鉴权仓库。复刻 Android repo/AuthRepository.kt。
 * Phase 2 全量：login/loginByContact/loginBySmsCode/me/updateSelfProfile/changeEmail/changePhone/verifyEmail/verifyPhone。
 */
export class AuthRepository {
  private managerApi: ManagerApi
  private tokenStorage: TokenStorage

  constructor(managerApi: ManagerApi, tokenStorage: TokenStorage) {
    this.managerApi = managerApi
    this.tokenStorage = tokenStorage
  }

  async login(username: string, password: string): Promise<UserInfo> {
    const req = new LoginRequest()
    req.username = username
    req.password = password
    const resp = await this.managerApi.login(req)
    await this.tokenStorage.save(new TokenData(resp.access_token, resp.refresh_token))
    return await this.managerApi.getMe()
  }

  async loginByContact(contact: string, contactType: string, password: string): Promise<UserInfo> {
    const req = new LoginByContactRequest()
    req.contact = contact
    req.contact_type = contactType
    req.password = password
    const resp = await this.managerApi.loginByContact(req)
    await this.tokenStorage.save(new TokenData(resp.access_token, resp.refresh_token))
    return await this.managerApi.getMe()
  }

  async loginBySmsCode(phone: string, code: string): Promise<UserInfo> {
    const req = new LoginBySmsCodeRequest()
    req.phone = phone
    req.code = code
    const resp = await this.managerApi.loginBySmsCode(req)
    await this.tokenStorage.save(new TokenData(resp.access_token, resp.refresh_token))
    return await this.managerApi.getMe()
  }

  async getCaptcha(): Promise<CaptchaResponse> {
    return await this.managerApi.getCaptcha()
  }

  async sendVerificationCode(
    channel: string, target: string, purpose: string, captchaId: string, captchaAnswer: string,
  ): Promise<VerificationCodeSendResponse> {
    const req = new VerificationCodeSendRequest()
    req.channel = channel
    req.target = target
    req.purpose = purpose
    req.captcha_id = captchaId
    req.captcha_answer = captchaAnswer
    return await this.managerApi.sendVerificationCode(req)
  }

  async verifyCode(channel: string, target: string, purpose: string, code: string): Promise<VerificationCodeVerifyResponse> {
    const req = new VerificationCodeVerifyRequest()
    req.channel = channel
    req.target = target
    req.purpose = purpose
    req.code = code
    return await this.managerApi.verifyCode(req)
  }

  async resetPassword(ticket: string, newPassword: string): Promise<void> {
    const req = new ResetPasswordRequest()
    req.ticket = ticket
    req.new_password = newPassword
    await this.managerApi.resetPassword(req)
  }

  async getVerificationChannels(): Promise<VerificationChannelsResponse> {
    return await this.managerApi.getVerificationChannels()
  }

  async me(): Promise<UserInfo> {
    return await this.managerApi.getMe()
  }

  async updateSelfProfile(
    realName: string | null = null,
    email: string | null = null,
    phone: string | null = null,
    avatarUrl: string | null = null,
  ): Promise<UserInfo> {
    const body = new UserSelfUpdateRequest()
    body.real_name = realName
    body.email = email
    body.phone = phone
    body.avatar_url = avatarUrl
    await this.managerApi.updateMe(body)
    return await this.managerApi.getMe()
  }

  async changeEmail(newEmail: string, code: string): Promise<void> {
    const req = new ChangeContactRequest()
    req.new_email = newEmail
    req.code = code
    await this.managerApi.changeEmail(req)
  }

  async changePhone(newPhone: string, code: string): Promise<void> {
    const req = new ChangeContactRequest()
    req.new_phone = newPhone
    req.code = code
    await this.managerApi.changePhone(req)
  }

  async verifyEmail(code: string): Promise<void> {
    const req = new UserVerifyCodeRequest()
    req.code = code
    await this.managerApi.verifyEmail(req)
  }

  async verifyPhone(code: string): Promise<void> {
    const req = new UserVerifyCodeRequest()
    req.code = code
    await this.managerApi.verifyPhone(req)
  }

  async logout(): Promise<void> {
    await this.tokenStorage.clear()
  }

  async getPresetAvatars(): Promise<PresetAvatarsResponse> {
    return await this.managerApi.getPresetAvatars()
  }

  async uploadAvatar(_bytes: Uint8Array, _filename: string): Promise<AvatarUploadResponse> {
    throw new Error('头像上传在 HarmonyOS 端暂未实现')
  }
}
