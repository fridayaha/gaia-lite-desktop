import { HttpClient, HttpResponse } from './HttpClient';
import { buildMultipartFileBody } from './Multipart';
import { LoginRequest, LoginResponse, RefreshRequest, RefreshResponse, UserInfo, CaptchaResponse, VerificationCodeSendRequest, VerificationCodeSendResponse, VerificationCodeVerifyRequest, VerificationCodeVerifyResponse, ResetPasswordRequest, VerificationChannelsResponse, PresetAvatarsResponse, UserSelfUpdateRequest, ChangeContactRequest, UserVerifyCodeRequest, LoginByContactRequest, LoginBySmsCodeRequest, AvatarUploadResponse } from './dto/AuthDtos';
import { AccessibleAgent, AgentDeploymentStatus, AgentModelsResponse } from './dto/AgentDtos';
import { AppReleaseLatest } from './dto/AppReleaseDtos';
import { FeedbackUpsertRequest, FeedbackItem, FavoriteUpsertRequest, FavoriteDeleteRequest, FavoriteItem } from './dto/FeedbackDtos';
import { CreateFolderRequest, MoveFileRequest, WorkspaceFileList, WorkspaceFileContent } from './dto/WorkspaceDtos';
import { UploadFileResponse } from './dto/ChatDtos';

/**
 * Manager API 手写实现。复刻 Android net/ManagerApi.kt 的 Retrofit 注解接口，
 * 但 ArkTS 无 Retrofit，每个 endpoint 显式调 HttpClient.request + JSON.parse。
 *
 * 路径与 Android 完全一致（manager path 不带前缀），由 HttpClient.resolveUrl 走 manager base。
 */
export class ManagerApi {
  private client: HttpClient

  constructor(client: HttpClient) {
    this.client = client
  }

  async login(body: LoginRequest): Promise<LoginResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/login', body: JSON.stringify(body), auth: false })
    return this.parse(r, LoginResponse.fromJson)
  }

  async loginByContact(body: LoginByContactRequest): Promise<LoginResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/login-by-contact', body: JSON.stringify(body), auth: false })
    return this.parse(r, LoginResponse.fromJson)
  }

  async loginBySmsCode(body: LoginBySmsCodeRequest): Promise<LoginResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/login-by-sms-code', body: JSON.stringify(body), auth: false })
    return this.parse(r, LoginResponse.fromJson)
  }

  async getCaptcha(): Promise<CaptchaResponse> {
    const r = await this.client.request({ method: 'GET', url: 'auth/captcha', auth: false })
    return this.parse(r, CaptchaResponse.fromJson)
  }

  async sendVerificationCode(body: VerificationCodeSendRequest): Promise<VerificationCodeSendResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/verification-code/send', body: JSON.stringify(body), auth: false })
    return this.parse(r, VerificationCodeSendResponse.fromJson)
  }

  async verifyCode(body: VerificationCodeVerifyRequest): Promise<VerificationCodeVerifyResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/verification-code/verify', body: JSON.stringify(body), auth: false })
    return this.parse(r, VerificationCodeVerifyResponse.fromJson)
  }

  async resetPassword(body: ResetPasswordRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: 'auth/reset-password', body: JSON.stringify(body), auth: false })
  }

  async getVerificationChannels(): Promise<VerificationChannelsResponse> {
    const r = await this.client.request({ method: 'GET', url: 'auth/verification-channels', auth: false })
    return this.parse(r, VerificationChannelsResponse.fromJson)
  }

  async getMe(): Promise<UserInfo> {
    const r = await this.client.request({ method: 'GET', url: 'auth/me' })
    return this.parse(r, UserInfo.fromJson)
  }

  async updateMe(body: UserSelfUpdateRequest): Promise<void> {
    await this.client.request({ method: 'PATCH', url: 'auth/me', body: JSON.stringify(body) })
  }

  async uploadAvatar(file: Uint8Array, filename: string, mime: string): Promise<AvatarUploadResponse> {
    const mp = buildMultipartFileBody(file, filename, mime)
    const r = await this.client.request({
      method: 'POST',
      url: 'auth/avatar',
      headers: { 'Content-Type': mp.contentType },
      bodyBytes: mp.body,
    })
    return this.parse(r, AvatarUploadResponse.fromJson)
  }

  async changeEmail(body: ChangeContactRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: 'auth/me/change-email', body: JSON.stringify(body) })
  }

  async changePhone(body: ChangeContactRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: 'auth/me/change-phone', body: JSON.stringify(body) })
  }

  async verifyEmail(body: UserVerifyCodeRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: 'auth/me/verify-email', body: JSON.stringify(body) })
  }

  async verifyPhone(body: UserVerifyCodeRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: 'auth/me/verify-phone', body: JSON.stringify(body) })
  }

  async getPresetAvatars(): Promise<PresetAvatarsResponse> {
    const r = await this.client.request({ method: 'GET', url: 'auth/preset-avatars' })
    return this.parse(r, PresetAvatarsResponse.fromJson)
  }

  async refresh(body: RefreshRequest): Promise<RefreshResponse> {
    const r = await this.client.request({ method: 'POST', url: 'auth/refresh', body: JSON.stringify(body), auth: false })
    return this.parse(r, RefreshResponse.fromJson)
  }

  async getAccessibleAgents(): Promise<AccessibleAgent[]> {
    const r = await this.client.request({ method: 'GET', url: 'agent-instances/accessible' })
    return this.parseArray(r, AccessibleAgent.fromJson)
  }

  async getDeploymentStatus(id: string): Promise<AgentDeploymentStatus> {
    const r = await this.client.request({ method: 'GET', url: `agent-instances/${id}/deployment-status` })
    return this.parse(r, AgentDeploymentStatus.fromJson)
  }

  async deploy(id: string): Promise<AgentDeploymentStatus> {
    const r = await this.client.request({ method: 'POST', url: `agent-instances/${id}/deploy` })
    return this.parse(r, AgentDeploymentStatus.fromJson)
  }

  async getModels(id: string): Promise<AgentModelsResponse> {
    const r = await this.client.request({ method: 'GET', url: `agent-instances/${id}/models` })
    return this.parse(r, AgentModelsResponse.fromJson)
  }

  async listFiles(id: string, path: string): Promise<WorkspaceFileList> {
    const r = await this.client.request({ method: 'GET', url: `agent-instances/${id}/files?path=${encodeURIComponent(path)}` })
    return this.parse(r, WorkspaceFileList.fromJson)
  }

  async readFile(id: string, path: string): Promise<WorkspaceFileContent> {
    const r = await this.client.request({ method: 'GET', url: `agent-instances/${id}/files/content?path=${encodeURIComponent(path)}` })
    return this.parse(r, WorkspaceFileContent.fromJson)
  }

  async uploadAgentFile(id: string, path: string, file: Uint8Array, filename: string, mime: string): Promise<UploadFileResponse> {
    const mp = buildMultipartFileBody(file, filename, mime)
    const r = await this.client.request({
      method: 'POST',
      url: `agent-instances/${id}/files/upload?path=${encodeURIComponent(path)}`,
      headers: { 'Content-Type': mp.contentType },
      bodyBytes: mp.body,
      timeoutMs: 120000,
    })
    return this.parse(r, UploadFileResponse.fromJson)
  }

  async createFolder(id: string, path: string, body: CreateFolderRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: `agent-instances/${id}/files/mkdir?path=${encodeURIComponent(path)}`, body: JSON.stringify(body) })
  }

  async deleteFile(id: string, path: string): Promise<void> {
    await this.client.request({ method: 'DELETE', url: `agent-instances/${id}/files?path=${encodeURIComponent(path)}` })
  }

  async moveFile(id: string, body: MoveFileRequest): Promise<void> {
    await this.client.request({ method: 'POST', url: `agent-instances/${id}/files/move`, body: JSON.stringify(body) })
  }

  /**
   * 下载二进制文件。返回 ArrayBuffer；失败抛 ApiError。
   * 不走 parse() JSON 路径，期望类型为 ArrayBuffer。
   */
  async downloadFile(id: string, path: string): Promise<ArrayBuffer> {
    const r = await this.client.requestBinary({
      method: 'GET',
      url: `agent-instances/${id}/files/download?path=${encodeURIComponent(path)}`,
    })
    if (!r.ok) throw new ApiError(r.status, '')
    return r.data
  }

  async getLatestRelease(): Promise<AppReleaseLatest | null> {
    const r = await this.client.request({ method: 'GET', url: 'public/app-releases/latest', auth: false })
    if (r.status === 404) return null
    return this.parse(r, AppReleaseLatest.fromJson)
  }

  async getReleaseByVersion(version: string): Promise<AppReleaseLatest | null> {
    const r = await this.client.request({ method: 'GET', url: `public/app-releases/by-version/${version}`, auth: false })
    if (r.status === 404) return null
    return this.parse(r, AppReleaseLatest.fromJson)
  }

  async upsertFeedback(body: FeedbackUpsertRequest): Promise<void> {
    await this.client.request({ method: 'PUT', url: 'message-feedback', body: JSON.stringify(body) })
  }

  async listFeedback(sessionId: string): Promise<FeedbackItem[]> {
    const r = await this.client.request({ method: 'GET', url: `message-feedback?session_id=${encodeURIComponent(sessionId)}` })
    return this.parseArray(r, FeedbackItem.fromJson)
  }

  async upsertFavorite(body: FavoriteUpsertRequest): Promise<FavoriteItem> {
    const r = await this.client.request({ method: 'PUT', url: 'message-favorites', body: JSON.stringify(body) })
    return this.parse(r, FavoriteItem.fromJson)
  }

  async deleteFavorite(body: FavoriteDeleteRequest): Promise<void> {
    await this.client.request({ method: 'DELETE', url: 'message-favorites', body: JSON.stringify(body) })
  }

  async listSessionFavorites(sessionId: string): Promise<FavoriteItem[]> {
    const r = await this.client.request({ method: 'GET', url: `message-favorites?session_id=${encodeURIComponent(sessionId)}` })
    return this.parseArray(r, FavoriteItem.fromJson)
  }

  async listMyFavorites(limit: number = 50, offset: number = 0): Promise<FavoriteItem[]> {
    const r = await this.client.request({ method: 'GET', url: `message-favorites/mine?limit=${limit}&offset=${offset}` })
    return this.parseArray(r, FavoriteItem.fromJson)
  }

  private parse<T>(r: HttpResponse, fromJson: (o: Record<string, Object>) => T): T {
    if (!r.ok) throw new ApiError(r.status, r.body)
    const parsed = JSON.parse(r.body) as Record<string, Object>
    return fromJson(parsed)
  }

  private parseArray<T>(r: HttpResponse, fromJson: (o: Record<string, Object>) => T): T[] {
    if (!r.ok) throw new ApiError(r.status, r.body)
    const parsed = JSON.parse(r.body) as Object
    if (!(parsed instanceof Array)) return []
    return parsed.filter((v: Object) => v instanceof Object)
      .map((v: Object) => fromJson(v as Record<string, Object>))
  }
}

export class ApiError extends Error {
  status: number
  body: string

  constructor(status: number, body: string) {
    super(`HTTP ${status}`)
    this.status = status
    this.body = body
  }
}
