/**
 * POST /v1/audio/transcriptions 响应（gateway ASR）。
 * text 缺省兜底空串。复刻 Android TranscribeResponse.kt。
 */

export class TranscribeResponse {
  text: string | null = null

  static fromJson(o: Record<string, Object>): TranscribeResponse {
    const r = new TranscribeResponse()
    if (typeof o['text'] === 'string') r.text = o['text']
    return r
  }
}
