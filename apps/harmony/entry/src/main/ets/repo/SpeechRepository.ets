import { GatewayApi } from '../net/GatewayApi';
import { TranscribeResponse } from '../net/dto/TranscribeResponse';

/**
 * 语音转写仓库。复刻 Android repo/SpeechRepository.kt。
 * 把 m4a 字节流转给 gateway /v1/audio/transcriptions，识别失败抛异常由调用方兜底。
 */
export class SpeechRepository {
  private gatewayApi: GatewayApi

  constructor(gatewayApi: GatewayApi) {
    this.gatewayApi = gatewayApi
  }

  async transcribe(audio: Uint8Array, format: string = 'm4a'): Promise<string> {
    const resp: TranscribeResponse = await this.gatewayApi.transcribeAudio(audio, format)
    const text = resp.text ?? ''
    return text.trim()
  }
}
