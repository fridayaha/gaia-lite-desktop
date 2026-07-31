import http from '@ohos.net.http';
import buffer from '@ohos.buffer';
import socket from '@ohos.net.socket';
import { HttpClient } from '../net/HttpClient';
import { TokenRefresher } from '../net/TokenRefresher';
import { SessionController } from '../net/SessionController';
import { logI, logE } from '../util/Logger';

/**
 * HarmonyOS SSE 流式读取 —— 用 raw TCP socket 实现 HTTP/1.1 长连接读流。
 *
 * 背景：@ohos.net.http 实测对 text/event-stream 响应不触发 dataReceive/dataEnd，
 * 整个 body 被一次性 buffer 到 resp.result，request Promise 等连接关闭才 resolve。
 * 表现：流式逐字显示无法实现，要等几十秒后一次性出现整段消息。
 *
 * 解决：用 @ohos.net.socket 自己实现 HTTP/1.1 GET/POST + chunked 读取。
 * socket 的 on('message') 按 TCP 段触发，能拿到真正的流式 chunk。
 *
 * 协议层一致：
 * - 帧级：data: <payload> 行的 payload 部分（去前缀、去 [DONE] 终止符）
 * - 401 处理：首响应若 status=401 → refresh() → 重试一次；失败 → forceLogout
 * - 调用方负责 JSON 解析 + 路由到 StreamEvent
 *
 * 仅支持 http://（手机端 manager/gateway 走 http 直连 nginx）。
 * https:// 需要 TLSSocket 实例，留待后续接入。
 */
export interface SseStreamOpts extends SseHandlers {
  url: string
  headers?: Record<string, string>
  method?: 'GET' | 'POST'
  body?: string
}

export interface SseHandlers {
  onPayload: (payload: string) => void
  onDone: () => void
  onError: (e: Error) => void
}

export interface SseHandle {
  cancel(): void
}

class SseHandleImpl implements SseHandle {
  private canceller: () => void
  constructor(canceller: () => void) {
    this.canceller = canceller
  }
  cancel(): void {
    this.canceller()
  }
}

/** URL 解析结果。仅支持 http://，不支持 https://。 */
interface ParsedUrl {
  host: string
  port: number
  path: string
}

function parseHttpUrl(url: string): ParsedUrl | null {
  const prefix = 'http://'
  if (!url.startsWith(prefix)) return null
  const rest = url.slice(prefix.length)
  const slashIdx = rest.indexOf('/')
  const hostPort = slashIdx >= 0 ? rest.slice(0, slashIdx) : rest
  const path = slashIdx >= 0 ? rest.slice(slashIdx) : '/'
  let host: string
  let port: number
  const colonIdx = hostPort.indexOf(':')
  if (colonIdx >= 0) {
    host = hostPort.slice(0, colonIdx)
    port = parseInt(hostPort.slice(colonIdx + 1), 10)
    if (isNaN(port)) port = 80
  } else {
    host = hostPort
    port = 80
  }
  return { host, port, path }
}

export class SseClient {
  private httpClient: HttpClient
  private tokenRefresher: TokenRefresher
  private sessionController: SessionController

  constructor(
    httpClient: HttpClient,
    tokenRefresher: TokenRefresher,
    sessionController: SessionController,
  ) {
    this.httpClient = httpClient
    this.tokenRefresher = tokenRefresher
    this.sessionController = sessionController
  }

  async stream(opts: SseStreamOpts): Promise<SseHandle> {
    const fullUrl = this.httpClient.resolveUrl(opts.url)
    const finalHeaders = await this.httpClient.buildSseHeaders(opts.headers ?? {})
    return await this.openSocketStream(fullUrl, finalHeaders, opts.method ?? 'GET', opts.body ?? '', opts)
  }

  private async openSocketStream(
    url: string,
    headers: Record<string, string>,
    method: 'GET' | 'POST',
    body: string,
    handlers: SseHandlers,
  ): Promise<SseHandle> {
    const parsed = parseHttpUrl(url)
    if (parsed === null) {
      handlers.onError(new Error(`SSE URL not http:// : ${url}`))
      return new SseHandleImpl(() => {})
    }
    const sock: socket.TCPSocket = socket.constructTCPSocketInstance()
    let cancelled = false
    let headerParsed = false
    let retrying = false
    let bodyBuf = ''
    // 解析响应时用，记录响应起始字节（去掉 HTTP 头之后）
    let needBytes = -1  // chunked: -1（持续读到连接关闭）；Content-Length: N
    let contentLengthRead = 0

    const handleFrame = (frame: string): void => {
      const lines = frame.split('\n')
      for (const rawLine of lines) {
        const line = rawLine.trim()
        if (line.length === 0 || line.startsWith(':')) continue
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') {
          try { handlers.onDone() } catch (e) {}
          return
        }
        try { handlers.onPayload(payload) } catch (e) {}
      }
    }

    const dispatchBody = (chunk: string): void => {
      bodyBuf += chunk
      let idx: number
      while ((idx = bodyBuf.indexOf('\n\n')) >= 0) {
        const frame = bodyBuf.slice(0, idx)
        bodyBuf = bodyBuf.slice(idx + 2)
        handleFrame(frame)
      }
    }

    const onMessage = (info: socket.SocketMessageInfo): void => {
      if (cancelled) return
      const arr = new Uint8Array(info.message)
      const chunk = buffer.from(arr.buffer, arr.byteOffset, arr.byteLength).toString('utf-8')
      if (!headerParsed) {
        const headerEnd = chunk.indexOf('\r\n\r\n')
        if (headerEnd < 0) {
          // 头还没完整，累积到 bodyBuf（复用作 header buf）
          bodyBuf += chunk
          return
        }
        const headerPart = chunk.slice(0, headerEnd)
        const bodyStart = chunk.slice(headerEnd + 4)
        const headerLines = headerPart.split('\r\n')
        const statusLine = headerLines[0] ?? ''
        const statusParts = statusLine.split(' ')
        const statusCode = parseInt(statusParts[1] ?? '0', 10)
        // 查 Content-Length / Transfer-Encoding
        for (let i = 1; i < headerLines.length; i++) {
          const hl = headerLines[i]!.toLowerCase()
          if (hl.startsWith('content-length:')) {
            needBytes = parseInt(hl.slice(15).trim(), 10)
          } else if (hl.startsWith('transfer-encoding:') && hl.includes('chunked')) {
            needBytes = -2 // chunked 模式标记
          }
        }
        bodyBuf = ''
        headerParsed = true
        logI('SseClient', `socket header code=${statusCode} len=${needBytes}`)
        if (statusCode === 401 && !retrying) {
          // 鉴权失败：刷新 + 重连一次
          retrying = true
          try { sock.close() } catch (e) {}
          this.tokenRefresher.refresh().then((refreshed: string | null) => {
            if (refreshed === null) {
              this.sessionController.emitForceLogout()
              handlers.onError(new Error('SSE_AUTH'))
              return
            }
            const headers2: Record<string, string> = {}
            const keys = Object.keys(headers)
            for (const k of keys) {
              headers2[k] = headers[k]
            }
            headers2['Authorization'] = `Bearer ${refreshed}`
            this.openSocketStream(url, headers2, method, body, handlers)
          })
          return
        }
        if (statusCode < 200 || statusCode >= 300) {
          handlers.onError(new Error(`SSE HTTP ${statusCode}`))
          try { sock.close() } catch (e) {}
          return
        }
        // 处理响应头后剩下的首块 body
        if (bodyStart.length > 0) {
          if (needBytes === -2) {
            // chunked：先按 chunked 解
            dispatchChunked(bodyStart)
          } else {
            contentLengthRead += bodyStart.length
            dispatchBody(bodyStart)
            if (needBytes >= 0 && contentLengthRead >= needBytes) {
              if (bodyBuf.length > 0) {
                handleFrame(bodyBuf)
                bodyBuf = ''
              }
              try { handlers.onDone() } catch (e) {}
              try { sock.close() } catch (e2) {}
            }
          }
        }
        return
      }
      // 已过头：直接处理 body 段
      if (needBytes === -2) {
        dispatchChunked(chunk)
      } else {
        contentLengthRead += chunk.length
        dispatchBody(chunk)
        if (needBytes >= 0 && contentLengthRead >= needBytes) {
          if (bodyBuf.length > 0) {
            handleFrame(bodyBuf)
            bodyBuf = ''
          }
          try { handlers.onDone() } catch (e) {}
          try { sock.close() } catch (e2) {}
        }
      }
    }

    // chunked 编码状态
    let chunkedBuf = ''
    let chunkSizeRemaining = -1

    const dispatchChunked = (chunk: string): void => {
      chunkedBuf += chunk
      while (chunkedBuf.length > 0) {
        if (chunkSizeRemaining <= 0) {
          // 读 chunk size 行
          const crlf = chunkedBuf.indexOf('\r\n')
          if (crlf < 0) return
          const sizeLine = chunkedBuf.slice(0, crlf)
          chunkedBuf = chunkedBuf.slice(crlf + 2)
          const semi = sizeLine.indexOf(';')
          const sizeStr = (semi >= 0 ? sizeLine.slice(0, semi) : sizeLine).trim()
          chunkSizeRemaining = parseInt(sizeStr, 16)
          if (isNaN(chunkSizeRemaining)) {
            handlers.onError(new Error(`SSE chunked bad size: ${sizeLine}`))
            try { sock.close() } catch (e) {}
            return
          }
          if (chunkSizeRemaining === 0) {
            // 末尾 chunk，等 trailing CRLF
            // 实际 body 应已收齐
            if (bodyBuf.length > 0) {
              handleFrame(bodyBuf)
              bodyBuf = ''
            }
            try { handlers.onDone() } catch (e) {}
            try { sock.close() } catch (e2) {}
            return
          }
        }
        if (chunkSizeRemaining > 0) {
          const take = Math.min(chunkSizeRemaining, chunkedBuf.length)
          const piece = chunkedBuf.slice(0, take)
          chunkedBuf = chunkedBuf.slice(take)
          chunkSizeRemaining -= take
          dispatchBody(piece)
        }
        // chunk 末尾 CRLF
        if (chunkSizeRemaining === 0 && chunkedBuf.length >= 2) {
          if (chunkedBuf.startsWith('\r\n')) {
            chunkedBuf = chunkedBuf.slice(2)
            // 继续下一个 chunk
          } else {
            break
          }
        } else if (chunkSizeRemaining === 0) {
          break  // 等下次再处理 CRLF
        }
      }
    }

    sock.on('message', onMessage)
    sock.on('close', () => {
      if (cancelled) return
      logI('SseClient', `socket close buf=${bodyBuf.length}`)
      if (bodyBuf.length > 0) {
        handleFrame(bodyBuf)
        bodyBuf = ''
      }
      try { handlers.onDone() } catch (e) {}
    })
    sock.on('error', (err: Error) => {
      if (cancelled) return
      logE('SseClient', `socket error: ${err.message ?? String(err)}`)
      handlers.onError(err instanceof Error ? err : new Error(String(err)))
    })

    // 构造原始 HTTP 请求
    const reqLines: string[] = []
    reqLines.push(`${method} ${parsed.path} HTTP/1.1`)
    reqLines.push(`Host: ${parsed.host}:${parsed.port}`)
    reqLines.push('Accept: text/event-stream')
    reqLines.push('Connection: keep-alive')
    const headerKeys = Object.keys(headers)
    for (const k of headerKeys) {
      // 跳过 Host/Accept/Connection（上面已设）
      const lk = k.toLowerCase()
      if (lk === 'host' || lk === 'accept' || lk === 'connection') continue
      reqLines.push(`${k}: ${headers[k]}`)
    }
    if (body.length > 0 && method === 'POST') {
      reqLines.push(`Content-Length: ${body.length}`)
    }
    reqLines.push('')
    reqLines.push('')
    const reqStr = reqLines.join('\r\n') + (body.length > 0 && method === 'POST' ? body : '')

    try {
      await sock.bind({ address: '0.0.0.0', port: 0, family: 1 })
      await sock.connect({
        address: { address: parsed.host, port: parsed.port, family: 1 },
        timeout: 30000,
      })
      logI('SseClient', `socket connected ${parsed.host}:${parsed.port} path=${parsed.path}`)
      await sock.send({ data: reqStr })
      logI('SseClient', `socket sent ${reqStr.length}B`)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      logE('SseClient', `socket setup fail: ${msg}`)
      handlers.onError(e instanceof Error ? e : new Error(String(e)))
      try { sock.close() } catch (e2) {}
      return new SseHandleImpl(() => {})
    }

    const canceller = (): void => {
      cancelled = true
      try { sock.close() } catch (e) {}
    }
    return new SseHandleImpl(canceller)
  }
}
