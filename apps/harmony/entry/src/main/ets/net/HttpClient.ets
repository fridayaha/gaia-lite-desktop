import http from '@ohos.net.http';
import buffer from '@ohos.buffer';
import { TokenStorage } from '../data/TokenStorage';
import { TokenRefresher } from './TokenRefresher';
import { AgentContext } from './AgentContext';
import { ServerConfigLike } from './ServerConfig';
import { logW } from '../util/Logger';

/**
 * HttpClient 出站请求包装：注入鉴权 + agent 头；401 自动 refresh + 重试一次。
 * 复刻 Android OkHttp Interceptor 链 + Authenticator 在 ArkTS 端的等价物。
 *
 * - 无 OkHttp Interceptor：鉴权/agent 头在 buildHeaders 里集中注入
 * - 401 Authenticator 等价：request 里检测 401 → TokenRefresher.refresh() → 重试一次
 * - SSE 路径不走 request()，由 SseClient 直接 buildSseHeaders() 后开流
 *
 * 用法：
 *   const r = await client.request({ method: 'POST', path: 'auth/login', body: JSON.stringify(req) })
 *   if (r.ok) { const data = JSON.parse(r.body) }
 */

export interface HttpRequestOpts {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** 相对路径或绝对 URL；相对路径走 baseUrl（manager/gateway 之一）。 */
  url: string
  headers?: Record<string, string>
  body?: string
  /** 二进制请求体（multipart 文件上传）。与 body 互斥，优先使用。 */
  bodyBytes?: ArrayBuffer
  timeoutMs?: number
  /** 是否需要鉴权（auth/login 不需要）。默认 true。 */
  auth?: boolean
}

export interface HttpResponse {
  status: number
  body: string
  get ok(): boolean
}

export interface HttpBinaryResponse {
  status: number
  data: ArrayBuffer
  get ok(): boolean
}

class HttpResponseImpl implements HttpResponse {
  status: number
  body: string

  constructor(status: number, body: string) {
    this.status = status
    this.body = body
  }
  get ok(): boolean {
    return this.status >= 200 && this.status < 300
  }
}

class HttpBinaryResponseImpl implements HttpBinaryResponse {
  status: number
  data: ArrayBuffer

  constructor(status: number, data: ArrayBuffer) {
    this.status = status
    this.data = data
  }
  get ok(): boolean {
    return this.status >= 200 && this.status < 300
  }
}

/** HttpClient 构造所需 serverConfig 形态。 */
export type HttpClientServerConfig = ServerConfigLike

export class HttpClient {
  /** SSE 用 GET；普通用任意方法 */
  private serverConfig: HttpClientServerConfig
  private tokenStorage: TokenStorage
  private agentContext: AgentContext
  private tokenRefresher: TokenRefresher

  constructor(
    serverConfig: HttpClientServerConfig,
    tokenStorage: TokenStorage,
    agentContext: AgentContext,
    tokenRefresher: TokenRefresher,
  ) {
    this.serverConfig = serverConfig
    this.tokenStorage = tokenStorage
    this.agentContext = agentContext
    this.tokenRefresher = tokenRefresher
  }

  /**
   * 发起普通请求（非 SSE）。注入鉴权 + agent 头；401 自动 refresh + 重试一次。
   * 默认走 manager；可通过完整 URL 直接打到 gateway。
   */
  async request(opts: HttpRequestOpts): Promise<HttpResponse> {
    const url = this.resolveUrl(opts.url)
    const headers = await this.buildHeaders(opts.auth ?? true, opts.headers ?? {})
    const resp1 = await this.doRequest(opts.method, url, headers, opts.body ?? '', opts.bodyBytes ?? null, opts.timeoutMs ?? 30000)
    if (resp1.status !== 401) return resp1

    // 401：尝试 refresh 后重试一次
    const refreshed = await this.tokenRefresher.refresh()
    if (refreshed === null) {
      logW('HttpClient', '401 refresh failed; returning 401')
      return resp1
    }
    const headers2 = await this.buildHeaders(opts.auth ?? true, opts.headers ?? {})
    return await this.doRequest(opts.method, url, headers2, opts.body ?? '', opts.bodyBytes ?? null, opts.timeoutMs ?? 30000)
  }

  /**
   * SSE 用的 header 构造（与 request() 共用一套注入逻辑）。
   * SSE 路径不自动 refresh；调用方负责在开流前预检 401。
   */
  async buildSseHeaders(extra: Record<string, string> = {}): Promise<Record<string, string>> {
    const merged: Record<string, string> = {}
    const keys = Object.keys(extra)
    for (const k of keys) {
      merged[k] = extra[k]
    }
    merged['Accept'] = 'text/event-stream'
    return await this.buildHeaders(true, merged)
  }

  /**
   * 二进制下载。期望响应体为 ArrayBuffer（PDF/图片/Office 文件）。
   * 401 自动 refresh + 重试一次。
   */
  async requestBinary(opts: HttpRequestOpts): Promise<HttpBinaryResponse> {
    const url = this.resolveUrl(opts.url)
    const headers = await this.buildHeaders(opts.auth ?? true, opts.headers ?? {})
    const resp1 = await this.doRequestBinary(opts.method, url, headers, opts.timeoutMs ?? 60000)
    if (resp1.status !== 401) return resp1
    const refreshed = await this.tokenRefresher.refresh()
    if (refreshed === null) {
      logW('HttpClient', '401 refresh failed; returning 401')
      return resp1
    }
    const headers2 = await this.buildHeaders(opts.auth ?? true, opts.headers ?? {})
    return await this.doRequestBinary(opts.method, url, headers2, opts.timeoutMs ?? 60000)
  }

  resolveUrl(input: string): string {
    if (input.startsWith('http://') || input.startsWith('https://')) return input
    // Gateway path 以 v1/ 开头走 gateway；否则走 manager
    if (input.startsWith('v1/')) {
      const base = this.serverConfig.gatewayUrl
      return base.endsWith('/') ? base + input : base + '/' + input
    }
    const base = this.serverConfig.managerUrl
    return base.endsWith('/') ? base + input : base + '/' + input
  }

  /**
   * 内部：实际发请求（无 401 重试逻辑）。ArkTS 端用 @ohos.net.http 一次性调用。
   */
  private async doRequest(
    method: string,
    url: string,
    headers: Record<string, string>,
    body: string,
    bodyBytes: ArrayBuffer | null,
    timeoutMs: number,
  ): Promise<HttpResponse> {
    const req = http.createHttp()
    try {
      const opts: http.HttpRequestOptions = {
        method: this.mapMethod(method),
        header: headers,
        expectDataType: http.HttpDataType.STRING,
        connectTimeout: timeoutMs,
        readTimeout: timeoutMs,
      }
      if (method !== 'GET' && method !== 'DELETE') {
        if (bodyBytes !== null && bodyBytes.byteLength > 0) {
          opts.extraData = bodyBytes
        } else if (body.length > 0) {
          opts.extraData = body
        }
      }
      const resp = await req.request(url, opts)
      const status = resp.responseCode
      const text = typeof resp.result === 'string' ? resp.result : ''
      return new HttpResponseImpl(status, text)
    } finally {
      req.destroy()
    }
  }

  private mapMethod(m: string): http.RequestMethod {
    switch (m) {
      case 'GET': return http.RequestMethod.GET
      case 'POST': return http.RequestMethod.POST
      case 'PUT': return http.RequestMethod.PUT
      case 'PATCH': return http.RequestMethod.PUT // HarmonyOS RequestMethod 无 PATCH，统一走 PUT
      case 'DELETE': return http.RequestMethod.DELETE
      default: return http.RequestMethod.GET
    }
  }

  /**
   * 内部：发起二进制下载请求。expectDataType=ARRAY_BUFFER。
   */
  private async doRequestBinary(
    method: string,
    url: string,
    headers: Record<string, string>,
    timeoutMs: number,
  ): Promise<HttpBinaryResponse> {
    const req = http.createHttp()
    try {
      const opts: http.HttpRequestOptions = {
        method: this.mapMethod(method),
        header: headers,
        expectDataType: http.HttpDataType.ARRAY_BUFFER,
        connectTimeout: timeoutMs,
        readTimeout: timeoutMs,
      }
      const resp = await req.request(url, opts)
      const status = resp.responseCode
      const data = resp.result instanceof ArrayBuffer ? resp.result : new ArrayBuffer(0)
      return new HttpBinaryResponseImpl(status, data)
    } finally {
      req.destroy()
    }
  }

  /**
   * 注入鉴权 + agent 头。Android 端由 AuthInterceptor + AgentHeaderInterceptor 拆成两条链；
   * ArkTS 无 Interceptor，集中在一个函数里完成。
   */
  private async buildHeaders(auth: boolean, extra: Record<string, string>): Promise<Record<string, string>> {
    const h: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Client-Type': 'harmony',
    }
    if (auth) {
      const access = await this.tokenStorage.getAccessToken()
      if (access !== null && access.length > 0) {
        h['Authorization'] = `Bearer ${access}`
      }
    }
    const state = this.agentContext.state
    if (state.agentId !== null) h['X-Agent-ID'] = state.agentId
    if (state.engineType !== null) h['X-Engine-Type'] = state.engineType
    if (state.sessionId !== null) h['X-Session-ID'] = state.sessionId
    // 调用方传入的 extra 覆盖默认
    for (const k of Object.keys(extra)) {
      h[k] = extra[k]
    }
    return h
  }
}

/**
 * 工具：Uint8Array → string。SSE dataReceive 回调里解码 chunk 用。
 */
export function decodeUtf8(buf: ArrayBuffer | Uint8Array): string {
  const arr = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  return buffer.from(arr.buffer, arr.byteOffset, arr.byteLength).toString('utf-8')
}
