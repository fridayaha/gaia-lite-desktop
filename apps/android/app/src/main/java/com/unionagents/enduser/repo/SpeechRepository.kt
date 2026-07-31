package com.unionagents.enduser.repo

import com.unionagents.enduser.net.GatewayApi
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * 语音转写 — gateway POST /v1/audio/transcriptions（ASR provider 由服务端配置）。
 * 识别失败/服务未配置时抛异常，文案由调用方兜底。
 */
@Singleton
class SpeechRepository @Inject constructor(
    private val gatewayApi: GatewayApi,
) {
    /** m4a 文件 → 识别文本（trim 后）。识别结果为空返回空串。 */
    suspend fun transcribe(file: File): String = withContext(Dispatchers.IO) {
        val bytes = file.readBytes()
        val body = bytes.toRequestBody("application/octet-stream".toMediaType())
        gatewayApi.transcribeAudio(body, format = "m4a").text?.trim().orEmpty()
    }
}
