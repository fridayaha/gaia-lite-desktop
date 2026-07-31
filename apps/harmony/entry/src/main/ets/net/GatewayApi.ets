import { HttpClient, HttpResponse } from './HttpClient';
import { CreateSessionRequest, CreateSessionResponse, UpdateTitleRequest, SessionListResponse, MessageListResponse, StartRunRequest, StartRunResponse, RunStatusResponse, ApprovalRequest, ApprovalResponse, Attachment } from './dto/ChatDtos';
import { AgentModelsResponse } from './dto/AgentDtos';
import { TranscribeResponse } from './dto/TranscribeResponse';
import { ApiError } from './ManagerApi';

/**
 * Gateway API 手写实现。复刻 Android net/GatewayApi.kt 的 Retrofit 注解接口。
 *
 * 路径全部以 v1/ 开头，HttpClient.resolveUrl 走 gateway base。
 */
export class GatewayApi {
  private client: HttpClient

  constructor(client: HttpClient) {
    this.client = client
  }

  async listSessions(limit: number = 50): Promise<SessionListResponse> {
    const r = await this.client.request({ method: 'GET', url: `v1/sessions?limit=${limit}` })
    return this.parse(r, SessionListResponse.fromJson)
  }

  async createSession(body: CreateSessionRequest): Promise<CreateSessionResponse> {
    const r = await this.client.request({ method: 'POST', url: 'v1/sessions', body: JSON.stringify(body) })
    return this.parse(r, CreateSessionResponse.fromJson)
  }

  async updateSession(id: string, body: UpdateTitleRequest): Promise<void> {
    await this.client.request({ method: 'PATCH', url: `v1/sessions/${id}`, body: JSON.stringify(body) })
  }

  async deleteSession(id: string): Promise<void> {
    await this.client.request({ method: 'DELETE', url: `v1/sessions/${id}` })
  }

  async listMessages(id: string): Promise<MessageListResponse> {
    const r = await this.client.request({ method: 'GET', url: `v1/sessions/${id}/messages` })
    return this.parse(r, MessageListResponse.fromJson)
  }

  async getEngineModels(): Promise<AgentModelsResponse> {
    const r = await this.client.request({ method: 'GET', url: 'v1/models' })
    return this.parse(r, AgentModelsResponse.fromJson)
  }

  /** Phase 5：HERMES run 生命周期 - 启动 run 拿 runId。 */
  async startRun(body: StartRunRequest): Promise<StartRunResponse> {
    const r = await this.client.request({ method: 'POST', url: 'v1/runs', body: JSON.stringify(body) })
    return this.parse(r, StartRunResponse.fromJson)
  }

  async getRunStatus(id: string): Promise<RunStatusResponse> {
    const r = await this.client.request({ method: 'GET', url: `v1/runs/${id}` })
    return this.parse(r, RunStatusResponse.fromJson)
  }

  async submitApproval(id: string, body: ApprovalRequest): Promise<ApprovalResponse> {
    const r = await this.client.request({ method: 'POST', url: `v1/runs/${id}/approval`, body: JSON.stringify(body) })
    return this.parse(r, ApprovalResponse.fromJson)
  }

  /**
   * 语音转写：录音字节流（m4a）→ gateway 走 ASR → 文本。
   * 复刻 Android GatewayApi.transcribeAudio：raw bytes + application/octet-stream，
   * format 走 query（gateway 直接 request.body() 读原始字节，不是 multipart）。
   */
  async transcribeAudio(audio: Uint8Array, format: string = 'm4a'): Promise<TranscribeResponse> {
    const ab = new ArrayBuffer(audio.length)
    new Uint8Array(ab).set(audio)
    const r = await this.client.request({
      method: 'POST',
      url: `v1/audio/transcriptions?format=${format}`,
      headers: { 'Content-Type': 'application/octet-stream' },
      bodyBytes: ab,
    })
    if (!r.ok) throw new ApiError(r.status, r.body)
    return TranscribeResponse.fromJson(JSON.parse(r.body) as Record<string, Object>)
  }

  private parse<T>(r: HttpResponse, fromJson: (o: Record<string, Object>) => T): T {
    if (!r.ok) throw new ApiError(r.status, r.body)
    const parsed = JSON.parse(r.body) as Record<string, Object>
    return fromJson(parsed)
  }

  /** 内部用：把 attachments 序列化为 Hermes run request 格式（供 ChatStreamRunner 用）。 */
  static attachmentsForRun(atts: Attachment[]): Record<string, Object>[] {
    const out: Record<string, Object>[] = []
    for (const a of atts) {
      const o: Record<string, Object> = {}
      o['name'] = a.name
      o['path'] = a.path
      o['is_image'] = a.is_image
      if (a.mime !== null) o['mime'] = a.mime
      if (a.size !== null) o['size'] = a.size
      if (a.thumbnail_url !== null) o['thumbnail_url'] = a.thumbnail_url
      out.push(o)
    }
    return out
  }
}
