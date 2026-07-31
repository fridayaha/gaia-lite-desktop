package com.unionagents.enduser.net

import com.unionagents.enduser.net.dto.AgentModelsResponse
import com.unionagents.enduser.net.dto.ApprovalRequest
import com.unionagents.enduser.net.dto.ApprovalResponse
import com.unionagents.enduser.net.dto.CreateSessionRequest
import com.unionagents.enduser.net.dto.CreateSessionResponse
import com.unionagents.enduser.net.dto.MessageListResponse
import com.unionagents.enduser.net.dto.RunStatusResponse
import com.unionagents.enduser.net.dto.SessionListResponse
import com.unionagents.enduser.net.dto.StartRunRequest
import com.unionagents.enduser.net.dto.StartRunResponse
import com.unionagents.enduser.net.dto.TranscribeResponse
import com.unionagents.enduser.net.dto.UpdateTitleRequest
import okhttp3.RequestBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface GatewayApi {
    @GET("v1/sessions")
    suspend fun listSessions(@Query("limit") limit: Int = 50): SessionListResponse

    @POST("v1/sessions")
    suspend fun createSession(@Body body: CreateSessionRequest): CreateSessionResponse

    @PATCH("v1/sessions/{id}")
    suspend fun updateSession(@Path("id") id: String, @Body body: UpdateTitleRequest)

    @DELETE("v1/sessions/{id}")
    suspend fun deleteSession(@Path("id") id: String)

    @GET("v1/sessions/{id}/messages")
    suspend fun listMessages(@Path("id") id: String): MessageListResponse

    @GET("v1/models")
    suspend fun getEngineModels(): AgentModelsResponse

    // 阶段 5：HERMES run 生命周期
    @POST("v1/runs")
    suspend fun startRun(@Body body: StartRunRequest): StartRunResponse

    @GET("v1/runs/{id}")
    suspend fun getRunStatus(@Path("id") id: String): RunStatusResponse

    @POST("v1/runs/{id}/approval")
    suspend fun submitApproval(@Path("id") id: String, @Body body: ApprovalRequest): ApprovalResponse

    // 语音转写：按住说话录音（m4a）→ 文本。不走引擎代理，gateway 直接调 ASR provider
    @POST("v1/audio/transcriptions")
    suspend fun transcribeAudio(
        @Body audio: RequestBody,
        @Query("format") format: String = "m4a",
    ): TranscribeResponse
}
