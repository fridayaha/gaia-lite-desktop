import common from '@ohos.app.ability.common';
import http from '@ohos.net.http';
import bundleManager from '@ohos.bundle.bundleManager';
import { BusinessError } from '@ohos.base';
import { ServerConfig } from '../net/ServerConfig';
import { TokenStorage } from '../data/TokenStorage';
import { LastAgentStore } from '../data/LastAgentStore';
import { TokenRefresher, RefreshResult } from '../net/TokenRefresher';
import { AgentContext } from '../net/AgentContext';
import { SessionController } from '../net/SessionController';
import { HttpClient } from '../net/HttpClient';
import { ManagerApi } from '../net/ManagerApi';
import { GatewayApi } from '../net/GatewayApi';
import { AuthRepository } from '../repo/AuthRepository';
import { AgentRepository } from '../repo/AgentRepository';
import { ChatRepository } from '../repo/ChatRepository';
import { WorkspaceRepository } from '../repo/WorkspaceRepository';
import { MessageFeedbackRepository } from '../repo/MessageFeedbackRepository';
import { AppReleaseRepository } from '../repo/AppReleaseRepository';
import { DeveloperModeStore } from '../repo/DeveloperModeStore';
import { AccountStore } from '../repo/AccountStore';
import { LastViewedStore } from '../repo/LastViewedStore';
import { SpeechPlayer } from '../repo/SpeechPlayer';
import { SpeechRepository } from '../repo/SpeechRepository';
import { HapDownloader } from '../repo/HapDownloader';
import { SseClient } from '../sse/SseClient';
import { ChatStreamRunner } from '../sse/ChatStreamRunner';
import { StreamProbe } from '../sse/StreamProbe';
import { logI } from '../util/Logger';

/**
 * DI 容器：在 EntryAbility.onCreate 里 new 出所有依赖。
 * 复刻 Android di/AppModule.kt + AppContainer（Hilt @Singleton 集合）。
 *
 * 不用 Hilt，不用 @Inject 注解——所有依赖都是显式 new，依赖关系一目了然。
 *
 * 持有顺序：先无依赖的（ServerConfig/TokenStorage/LastAgentStore/TokenRefresher/AgentContext），
 * 再有依赖的（SessionController → HttpClient → ManagerApi/GatewayApi → Repository → SseClient → ChatStreamRunner）。
 */
export class AppContainer {
  readonly contextAbility: common.UIAbilityContext
  readonly serverConfig: ServerConfig
  readonly tokenStorage: TokenStorage
  readonly lastAgentStore: LastAgentStore
  readonly lastViewedStore: LastViewedStore
  readonly developerModeStore: DeveloperModeStore
  readonly accountStore: AccountStore
  readonly tokenRefresher: TokenRefresher
  readonly agentContext: AgentContext
  readonly sessionController: SessionController
  readonly httpClient: HttpClient
  readonly managerApi: ManagerApi
  readonly gatewayApi: GatewayApi
  readonly authRepository: AuthRepository
  readonly agentRepository: AgentRepository
  readonly chatRepository: ChatRepository
  readonly workspaceRepository: WorkspaceRepository
  readonly messageFeedbackRepository: MessageFeedbackRepository
  readonly appReleaseRepository: AppReleaseRepository
  readonly speechPlayer: SpeechPlayer
  readonly speechRepository: SpeechRepository
  readonly hapDownloader: HapDownloader
  readonly sseClient: SseClient
  readonly chatStreamRunner: ChatStreamRunner

  constructor(context: common.UIAbilityContext) {
    this.contextAbility = context
    this.serverConfig = new ServerConfig(context)
    this.tokenStorage = new TokenStorage(context)
    this.lastAgentStore = new LastAgentStore(context)
    this.lastViewedStore = new LastViewedStore(context)
    this.developerModeStore = new DeveloperModeStore(context)
    this.accountStore = new AccountStore(context)

    // TokenRefresher 需要 refreshExecutor：调 manager /auth/refresh
    // 循环依赖（ManagerApi→HttpClient→TokenRefresher），refreshExecutor 走裸 @ohos.net.http 解决
    this.tokenRefresher = new TokenRefresher(this.tokenStorage, async (refreshToken) => {
      return await this.refreshDirect(refreshToken)
    })

    this.agentContext = new AgentContext(this.lastAgentStore)
    this.sessionController = new SessionController(this.tokenStorage, this.tokenRefresher)

    this.httpClient = new HttpClient(
      this.serverConfig,
      this.tokenStorage,
      this.agentContext,
      this.tokenRefresher,
    )

    this.managerApi = new ManagerApi(this.httpClient)
    this.gatewayApi = new GatewayApi(this.httpClient)
    this.authRepository = new AuthRepository(this.managerApi, this.tokenStorage)
    this.agentRepository = new AgentRepository(this.managerApi)
    this.chatRepository = new ChatRepository(this.gatewayApi, this.managerApi)
    this.workspaceRepository = new WorkspaceRepository(this.managerApi)
    this.messageFeedbackRepository = new MessageFeedbackRepository(this.managerApi)
    this.appReleaseRepository = new AppReleaseRepository(this.managerApi)
    this.speechPlayer = new SpeechPlayer()
    this.speechRepository = new SpeechRepository(this.gatewayApi)
    this.hapDownloader = new HapDownloader(context)

    this.sseClient = new SseClient(this.httpClient, this.tokenRefresher, this.sessionController)
    this.chatStreamRunner = new ChatStreamRunner(this.sseClient, this.gatewayApi)

    // StreamProbe 初始化（filesDir/logs/stream-probe.log）
    const filesDir = context.filesDir
    StreamProbe.init(`${filesDir}/logs/stream-probe.log`)
    StreamProbe.appStart(`harmony ${getAppVersion()}`)
    logI('AppContainer', 'initialized')
  }

  /**
   * 启动期恢复：从 LastAgentStore 读 last agent → AgentContext；调 SessionController.ensureSession。
   * DeveloperModeStore / AccountStore 的 prefs 值也一并 restore 到内存缓存。
   * 由 EntryAbility.onCreate 调用。
   */
  async onLaunch(): Promise<void> {
    await this.agentContext.restoreFromStore()
    await this.sessionController.ensureSession()
    await this.developerModeStore.restore()
    await this.accountStore.restore()
  }

  /** 裸调 /auth/refresh，不经过 HttpClient（避免循环）。 */
  private async refreshDirect(refreshToken: string): Promise<RefreshResult | null> {
    try {
      const req = http.createHttp()
      try {
        const body = JSON.stringify({ refresh_token: refreshToken })
        const opts: http.HttpRequestOptions = {
          method: http.RequestMethod.POST,
          header: { 'Content-Type': 'application/json' },
          expectDataType: http.HttpDataType.STRING,
          connectTimeout: 15000,
          readTimeout: 15000,
          extraData: body,
        }
        const url = this.serverConfig.managerUrl.replace(/\/$/, '') + '/auth/refresh'
        const resp = await req.request(url, opts)
        if (resp.responseCode < 200 || resp.responseCode >= 300) return null
        const text = typeof resp.result === 'string' ? resp.result : ''
        const parsed = JSON.parse(text) as Record<string, Object>
        const access = typeof parsed['access_token'] === 'string' ? parsed['access_token'] as string : null
        if (access === null) return null
        const refresh = typeof parsed['refresh_token'] === 'string' ? parsed['refresh_token'] as string : null
        const result: RefreshResult = { access, refresh }
        return result
      } finally {
        req.destroy()
      }
    } catch (e) {
      return null
    }
  }
}

function getAppVersion(): string {
  try {
    const info = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_DEFAULT)
    return info.versionName
  } catch (e) {
    return 'unknown'
  }
}
