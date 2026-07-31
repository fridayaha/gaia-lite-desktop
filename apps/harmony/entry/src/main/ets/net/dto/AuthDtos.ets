/**
 * 鉴权相关 DTO。复刻 Android net/dto/AuthDtos.kt。
 * 字段名一律用 snake_case 对齐服务端 JSON，避免 ArkTS 端写映射层。
 * JSON.stringify(class instance) 直接产出 snake_case JSON；fromJson 显式逐字段赋值。
 */

export class LoginRequest {
  username: string = ''
  password: string = ''
}

export class LoginResponse {
  access_token: string = ''
  refresh_token: string = ''
  token_type: string = 'bearer'

  static fromJson(o: Record<string, Object>): LoginResponse {
    const r = new LoginResponse()
    if (typeof o['access_token'] === 'string') r.access_token = o['access_token']
    if (typeof o['refresh_token'] === 'string') r.refresh_token = o['refresh_token']
    if (typeof o['token_type'] === 'string') r.token_type = o['token_type']
    return r
  }
}

export class RefreshRequest {
  refresh_token: string = ''
}

export class RefreshResponse {
  access_token: string = ''
  refresh_token: string | null = null

  static fromJson(o: Record<string, Object>): RefreshResponse {
    const r = new RefreshResponse()
    if (typeof o['access_token'] === 'string') r.access_token = o['access_token']
    if (typeof o['refresh_token'] === 'string') r.refresh_token = o['refresh_token']
    return r
  }
}

export class UserInfo {
  id: string | null = null
  username: string = ''
  email: string | null = null
  phone: string | null = null
  real_name: string | null = null
  avatar_url: string | null = null
  email_verified: boolean = false
  phone_verified: boolean = false

  static fromJson(o: Record<string, Object>): UserInfo {
    const r = new UserInfo()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['username'] === 'string') r.username = o['username']
    if (typeof o['email'] === 'string') r.email = o['email']
    if (typeof o['phone'] === 'string') r.phone = o['phone']
    if (typeof o['real_name'] === 'string') r.real_name = o['real_name']
    if (typeof o['avatar_url'] === 'string') r.avatar_url = o['avatar_url']
    if (typeof o['email_verified'] === 'boolean') r.email_verified = o['email_verified']
    if (typeof o['phone_verified'] === 'boolean') r.phone_verified = o['phone_verified']
    return r
  }
}

export class UserSelfUpdateRequest {
  real_name: string | null = null
  email: string | null = null
  phone: string | null = null
  avatar_url: string | null = null
}

export class AvatarUploadResponse {
  avatar_url: string = ''

  static fromJson(o: Record<string, Object>): AvatarUploadResponse {
    const r = new AvatarUploadResponse()
    if (typeof o['avatar_url'] === 'string') r.avatar_url = o['avatar_url']
    return r
  }
}

export class ChangeContactRequest {
  code: string = ''
  new_email: string | null = null
  new_phone: string | null = null
}

export class UserVerifyCodeRequest {
  code: string = ''
}

export class LoginByContactRequest {
  contact: string = ''
  contact_type: string = '' // "email" | "phone"
  password: string = ''
  captcha_id: string | null = null
  captcha_answer: string | null = null
}

export class LoginBySmsCodeRequest {
  phone: string = ''
  code: string = ''
}

export class CaptchaResponse {
  captcha_id: string = ''
  image_base64: string = ''

  static fromJson(o: Record<string, Object>): CaptchaResponse {
    const r = new CaptchaResponse()
    if (typeof o['captcha_id'] === 'string') r.captcha_id = o['captcha_id']
    if (typeof o['image_base64'] === 'string') r.image_base64 = o['image_base64']
    return r
  }
}

export class VerificationCodeSendRequest {
  channel: string = '' // "email" | "sms"
  target: string = ''
  purpose: string = '' // "reset_password" | "login" | ...
  captcha_id: string = ''
  captcha_answer: string = ''
}

export class VerificationCodeSendResponse {
  sent: boolean = false
  expires_in: number | null = null

  static fromJson(o: Record<string, Object>): VerificationCodeSendResponse {
    const r = new VerificationCodeSendResponse()
    if (typeof o['sent'] === 'boolean') r.sent = o['sent']
    if (typeof o['expires_in'] === 'number') r.expires_in = o['expires_in']
    return r
  }
}

export class VerificationCodeVerifyRequest {
  channel: string = ''
  target: string = ''
  purpose: string = ''
  code: string = ''
}

export class VerificationCodeVerifyResponse {
  verified: boolean = false
  ticket: string | null = null

  static fromJson(o: Record<string, Object>): VerificationCodeVerifyResponse {
    const r = new VerificationCodeVerifyResponse()
    if (typeof o['verified'] === 'boolean') r.verified = o['verified']
    if (typeof o['ticket'] === 'string') r.ticket = o['ticket']
    return r
  }
}

export class ResetPasswordRequest {
  ticket: string = ''
  new_password: string = ''
}

export class VerificationChannelsResponse {
  email: boolean = false
  sms: boolean = false

  static fromJson(o: Record<string, Object>): VerificationChannelsResponse {
    const r = new VerificationChannelsResponse()
    if (typeof o['email'] === 'boolean') r.email = o['email']
    if (typeof o['sms'] === 'boolean') r.sms = o['sms']
    return r
  }
}

export class PresetAvatarsData {
  items: string[] = []

  static fromJson(o: Record<string, Object>): PresetAvatarsData {
    const r = new PresetAvatarsData()
    const items = o['items']
    if (items instanceof Array) {
      r.items = items.filter((v: Object) => typeof v === 'string').map((v: Object) => v as string)
    }
    return r
  }
}

export class PresetAvatarsResponse {
  code: number = 0
  message: string | null = null
  data: PresetAvatarsData | null = null

  static fromJson(o: Record<string, Object>): PresetAvatarsResponse {
    const r = new PresetAvatarsResponse()
    if (typeof o['code'] === 'number') r.code = o['code']
    if (typeof o['message'] === 'string') r.message = o['message']
    const data = o['data']
    if (data instanceof Object) {
      r.data = PresetAvatarsData.fromJson(data as Record<string, Object>)
    }
    return r
  }
}
