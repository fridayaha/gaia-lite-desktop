import { AuthRepository } from '../../repo/AuthRepository';
import { AgentContext } from '../../net/AgentContext';
import { AppReleaseRepository } from '../../repo/AppReleaseRepository';
import { MessageFeedbackRepository } from '../../repo/MessageFeedbackRepository';
import { DeveloperModeStore } from '../../repo/DeveloperModeStore';
import { AccountStore, SavedAccount } from '../../repo/AccountStore';
import { HapDownloader } from '../../repo/HapDownloader';
import { TokenStorage, TokenData } from '../../data/TokenStorage';
import { MineUiState } from './MineUiState';
import { AppReleaseLatest } from '../../net/dto/AppReleaseDtos';
import { isVersionNewer } from '../../util/VersionUtil';
import { ServerConfig } from '../../net/ServerConfig';
import bundleManager from '@ohos.bundle.bundleManager';
import { logE } from '../../util/Logger';

/**
 * 我的页 ViewModel。复刻 Android ui/mine/MineViewModel.kt。
 *
 * Phase 2 全量：用户/版本/收藏/验证码渠道/预置头像 + 开发者模式 + 多账号 + 检查更新 +
 * 下载安装 + 个人资料（改姓名/邮箱/手机）+ 头像上传 + 改绑邮箱/手机 + 认证邮箱/手机 + 切换账号。
 */
export class MineViewModel {
  private _state: MineUiState = new MineUiState()
  private listeners: Array<(s: MineUiState) => void> = []
  private hapListener: (progress: number | null, error: string | null) => void = (p, e) => {
    if (e !== null) this.setState((s) => { s.downloadProgress = null; s.updateError = e })
    else if (p !== null) this.setState((s) => {
      s.downloadProgress = p
      if (p >= 1) s.downloadProgress = null // 下载完成归位
    })
  }
  private authRepo: AuthRepository
  private agentContext: AgentContext
  private appReleaseRepo: AppReleaseRepository
  private messageFeedbackRepo: MessageFeedbackRepository
  private developerModeStore: DeveloperModeStore
  private accountStore: AccountStore
  private hapDownloader: HapDownloader
  private serverConfig: ServerConfig
  private tokenStorage: TokenStorage

  constructor(
    authRepo: AuthRepository,
    agentContext: AgentContext,
    appReleaseRepo: AppReleaseRepository,
    messageFeedbackRepo: MessageFeedbackRepository,
    developerModeStore: DeveloperModeStore,
    accountStore: AccountStore,
    hapDownloader: HapDownloader,
    serverConfig: ServerConfig,
    tokenStorage: TokenStorage,
  ) {
    this.authRepo = authRepo
    this.agentContext = agentContext
    this.appReleaseRepo = appReleaseRepo
    this.messageFeedbackRepo = messageFeedbackRepo
    this.developerModeStore = developerModeStore
    this.accountStore = accountStore
    this.hapDownloader = hapDownloader
    this.serverConfig = serverConfig
    this.tokenStorage = tokenStorage
    this._state.appVersion = currentAppVersion()
  }

  get state(): MineUiState {
    return this._state
  }

  subscribe(listener: (s: MineUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: MineUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: MineUiState) => void): void {
    const next = this.cloneState()
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) {
        logE('MineViewModel', `notify failed: ${e}`)
      }
    }
  }

  private cloneState(): MineUiState {
    const s = new MineUiState()
    s.user = this._state.user
    s.appVersion = this._state.appVersion
    s.latestRelease = this._state.latestRelease
    s.currentVersionRelease = this._state.currentVersionRelease
    s.checkingUpdate = this._state.checkingUpdate
    s.updateAvailable = this._state.updateAvailable
    s.upToDate = this._state.upToDate
    s.updateError = this._state.updateError
    s.downloadProgress = this._state.downloadProgress
    s.recentFavorites = this._state.recentFavorites.slice()
    s.savedAccounts = this._state.savedAccounts.slice()
    s.switchingAccount = this._state.switchingAccount
    s.emailChannelEnabled = this._state.emailChannelEnabled
    s.smsChannelEnabled = this._state.smsChannelEnabled
    s.profileSaving = this._state.profileSaving
    s.profileError = this._state.profileError
    s.avatarUploading = this._state.avatarUploading
    s.contactVerifying = this._state.contactVerifying
    s.presetAvatars = this._state.presetAvatars.slice()
    s.developerMode = this._state.developerMode
    return s
  }

  /** 启动时拉所有数据 + 订阅 developerMode/accountsFlow。在 MineScreen aboutToAppear 调。 */
  async bootstrap(): Promise<void> {
    this.developerModeStore.subscribe((enabled) => {
      this.setState((s) => { s.developerMode = enabled })
    })
    this.accountStore.subscribe((accounts) => {
      this.setState((s) => { s.savedAccounts = accounts })
    })
    await Promise.all([
      this.fetchMe(),
      this.fetchLatestRelease(),
      this.fetchCurrentVersionRelease(),
      this.fetchVerificationChannels(),
      this.fetchPresetAvatars(),
    ])
    await this.loadRecentFavorites()
  }

  private async fetchMe(): Promise<void> {
    try {
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me })
    } catch (e) {
      logE('MineViewModel', `fetchMe failed: ${e}`)
    }
  }

  private async fetchLatestRelease(): Promise<void> {
    try {
      const latest = await this.appReleaseRepo.getLatestRelease()
      this.setState((s) => { s.latestRelease = latest })
    } catch (e) {}
  }

  private async fetchCurrentVersionRelease(): Promise<void> {
    try {
      const current = await this.appReleaseRepo.getReleaseByVersion(this._state.appVersion)
      this.setState((s) => { s.currentVersionRelease = current })
    } catch (e) {}
  }

  private async fetchVerificationChannels(): Promise<void> {
    try {
      const ch = await this.authRepo.getVerificationChannels()
      this.setState((s) => {
        s.emailChannelEnabled = ch.email
        s.smsChannelEnabled = ch.sms
      })
    } catch (e) {}
  }

  private async fetchPresetAvatars(): Promise<void> {
    try {
      const presets = await this.authRepo.getPresetAvatars()
      const items = presets.data !== null ? presets.data.items : []
      this.setState((s) => { s.presetAvatars = items })
    } catch (e) {}
  }

  async loadRecentFavorites(): Promise<void> {
    try {
      const items = await this.messageFeedbackRepo.listMyFavorites(10, 0)
      this.setState((s) => { s.recentFavorites = items })
    } catch (e) {}
  }

  async checkForUpdate(): Promise<void> {
    if (this._state.checkingUpdate) return
    this.setState((s) => {
      s.checkingUpdate = true
      s.updateError = null
      s.updateAvailable = false
      s.upToDate = false
    })
    try {
      const latest = await this.appReleaseRepo.getLatestRelease()
      this.setState((s) => { s.latestRelease = latest; s.checkingUpdate = false })
      if (latest === null || latest.version === null || latest.version.length === 0) {
        this.setState((s) => { s.updateError = '暂无可用的更新' })
      } else if (isVersionNewer(latest.version, this._state.appVersion)) {
        this.setState((s) => { s.updateAvailable = true })
      } else {
        this.setState((s) => { s.upToDate = true })
      }
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '检查更新失败') : '检查更新失败'
      this.setState((s) => { s.checkingUpdate = false; s.updateError = msg })
    }
  }

  dismissUpdateDialog(): void {
    this.setState((s) => {
      s.updateAvailable = false
      s.upToDate = false
      s.updateError = null
    })
  }

  clearUpdateError(): void {
    this.setState((s) => { s.updateError = null })
  }

  async setDeveloperMode(enabled: boolean): Promise<void> {
    await this.developerModeStore.set(enabled)
  }

  async startUpdateDownload(): Promise<void> {
    const release = this._state.latestRelease
    if (release === null || release.id === null || release.id.length === 0) return
    if (release.version === null || release.version.length === 0) return
    const url = `${this.serverConfig.managerUrl.replace(/\/$/, '')}/public/app-releases/${release.id}/apk`
    this.hapDownloader.subscribe(this.hapListener)
    const taskId = await this.hapDownloader.startDownload(url, release.version)
    if (taskId.length === 0) {
      this.setState((s) => { s.updateError = '下载启动失败' })
      return
    }
    this.setState((s) => { s.downloadProgress = 0 })
  }

  async logout(onDone: () => void): Promise<void> {
    await this.agentContext.clear()
    await this.authRepo.logout()
    onDone()
  }

  async switchAccount(userId: string, onDone: () => void): Promise<void> {
    if (this._state.switchingAccount) return
    this.setState((s) => { s.switchingAccount = true })
    try {
      await this.agentContext.clear()
      // ArkTS 端没有 authRepository.switchToAccount 等价 API（需要 managerApi 实现），
      // Phase 2 简化：直接从 AccountStore 取 token 写入 TokenStorage 后调 me() 刷新。
      const acc = await this.accountStore.getAccount(userId)
      if (acc === null) {
        this.setState((s) => { s.switchingAccount = false; s.updateError = '账号未保存，请重新登录' })
        return
      }
      // 直接覆写 token
      await this.tokenStorage.save(new TokenData(acc.access_token, acc.refresh_token))
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me; s.switchingAccount = false })
      onDone()
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '切换账号失败') : '切换账号失败'
      this.setState((s) => { s.switchingAccount = false; s.updateError = msg })
    }
  }

  async forgetAccount(userId: string): Promise<void> {
    await this.accountStore.removeAccount(userId)
  }

  clearProfileError(): void {
    this.setState((s) => { s.profileError = null })
  }

  /** 改真实姓名/邮箱/手机（单字段编辑）。 */
  async updateProfile(
    realName: string | null = null,
    email: string | null = null,
    phone: string | null = null,
  ): Promise<void> {
    if (this._state.profileSaving) return
    this.setState((s) => { s.profileSaving = true; s.profileError = null })
    try {
      const me = await this.authRepo.updateSelfProfile(realName, email, phone)
      this.setState((s) => { s.user = me; s.profileSaving = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '保存失败') : '保存失败'
      this.setState((s) => { s.profileSaving = false; s.profileError = msg })
    }
  }

  /** 选预置头像（不走文件上传，直接 PATCH avatar_url=presetUrl）。 */
  async selectPresetAvatar(presetUrl: string): Promise<void> {
    if (this._state.avatarUploading) return
    this.setState((s) => { s.avatarUploading = true; s.profileError = null })
    try {
      const me = await this.authRepo.updateSelfProfile(null, null, null, presetUrl)
      this.setState((s) => { s.user = me; s.avatarUploading = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '头像设置失败') : '头像设置失败'
      this.setState((s) => { s.avatarUploading = false; s.profileError = msg })
    }
  }

  async changeEmail(newEmail: string, code: string): Promise<void> {
    if (this._state.contactVerifying) return
    this.setState((s) => { s.contactVerifying = true; s.profileError = null })
    try {
      await this.authRepo.changeEmail(newEmail, code)
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me; s.contactVerifying = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '改绑邮箱失败') : '改绑邮箱失败'
      this.setState((s) => { s.contactVerifying = false; s.profileError = msg })
    }
  }

  async changePhone(newPhone: string, code: string): Promise<void> {
    if (this._state.contactVerifying) return
    this.setState((s) => { s.contactVerifying = true; s.profileError = null })
    try {
      await this.authRepo.changePhone(newPhone, code)
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me; s.contactVerifying = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '改绑手机失败') : '改绑手机失败'
      this.setState((s) => { s.contactVerifying = false; s.profileError = msg })
    }
  }

  async verifyEmail(code: string): Promise<void> {
    if (this._state.contactVerifying) return
    this.setState((s) => { s.contactVerifying = true; s.profileError = null })
    try {
      await this.authRepo.verifyEmail(code)
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me; s.contactVerifying = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '认证失败') : '认证失败'
      this.setState((s) => { s.contactVerifying = false; s.profileError = msg })
    }
  }

  async verifyPhone(code: string): Promise<void> {
    if (this._state.contactVerifying) return
    this.setState((s) => { s.contactVerifying = true; s.profileError = null })
    try {
      await this.authRepo.verifyPhone(code)
      const me = await this.authRepo.me()
      this.setState((s) => { s.user = me; s.contactVerifying = false })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '认证失败') : '认证失败'
      this.setState((s) => { s.contactVerifying = false; s.profileError = msg })
    }
  }
}

function currentAppVersion(): string {
  try {
    const info = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_DEFAULT)
    return info.versionName
  } catch (e) {
    return 'unknown'
  }
}
