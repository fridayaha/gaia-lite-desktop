package com.unionagents.enduser.net

import com.unionagents.enduser.net.dto.AccessibleAgent
import com.unionagents.enduser.net.dto.AgentDeploymentStatus
import com.unionagents.enduser.net.dto.AgentModelsResponse
import com.unionagents.enduser.net.dto.AppReleaseLatest
import com.unionagents.enduser.net.dto.CaptchaResponse
import com.unionagents.enduser.net.dto.ChangeContactRequest
import com.unionagents.enduser.net.dto.CreateFolderRequest
import com.unionagents.enduser.net.dto.FavoriteDeleteRequest
import com.unionagents.enduser.net.dto.FavoriteItem
import com.unionagents.enduser.net.dto.FavoriteUpsertRequest
import com.unionagents.enduser.net.dto.FeedbackItem
import com.unionagents.enduser.net.dto.FeedbackUpsertRequest
import com.unionagents.enduser.net.dto.LoginByContactRequest
import com.unionagents.enduser.net.dto.LoginBySmsCodeRequest
import com.unionagents.enduser.net.dto.LoginRequest
import com.unionagents.enduser.net.dto.LoginResponse
import com.unionagents.enduser.net.dto.MoveFileRequest
import com.unionagents.enduser.net.dto.PresetAvatarsResponse
import com.unionagents.enduser.net.dto.RefreshRequest
import com.unionagents.enduser.net.dto.RefreshResponse
import okhttp3.MultipartBody
import okhttp3.ResponseBody
import com.unionagents.enduser.net.dto.ResetPasswordRequest
import com.unionagents.enduser.net.dto.UploadFileResponse
import com.unionagents.enduser.net.dto.UserInfo
import com.unionagents.enduser.net.dto.UserSelfUpdateRequest
import com.unionagents.enduser.net.dto.UserVerifyCodeRequest
import com.unionagents.enduser.net.dto.VerificationChannelsResponse
import com.unionagents.enduser.net.dto.VerificationCodeSendRequest
import com.unionagents.enduser.net.dto.VerificationCodeSendResponse
import com.unionagents.enduser.net.dto.VerificationCodeVerifyRequest
import com.unionagents.enduser.net.dto.VerificationCodeVerifyResponse
import com.unionagents.enduser.net.dto.WorkspaceFileContent
import com.unionagents.enduser.net.dto.WorkspaceFileList
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.HTTP
import retrofit2.http.Multipart
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

interface ManagerApi {
    @POST("auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @POST("auth/login-by-contact")
    suspend fun loginByContact(@Body body: LoginByContactRequest): LoginResponse

    @POST("auth/login-by-sms-code")
    suspend fun loginBySmsCode(@Body body: LoginBySmsCodeRequest): LoginResponse

    @GET("auth/captcha")
    suspend fun getCaptcha(): CaptchaResponse

    @POST("auth/verification-code/send")
    suspend fun sendVerificationCode(@Body body: VerificationCodeSendRequest): VerificationCodeSendResponse

    @POST("auth/verification-code/verify")
    suspend fun verifyCode(@Body body: VerificationCodeVerifyRequest): VerificationCodeVerifyResponse

    @POST("auth/reset-password")
    suspend fun resetPassword(@Body body: ResetPasswordRequest)

    @GET("auth/verification-channels")
    suspend fun getVerificationChannels(): VerificationChannelsResponse

    @GET("auth/me")
    suspend fun getMe(): UserInfo

    @PATCH("auth/me")
    suspend fun updateMe(@Body body: UserSelfUpdateRequest)

    @Multipart
    @POST("auth/avatar")
    suspend fun uploadAvatar(@Part file: MultipartBody.Part)

    @POST("auth/me/change-email")
    suspend fun changeEmail(@Body body: ChangeContactRequest)

    @POST("auth/me/change-phone")
    suspend fun changePhone(@Body body: ChangeContactRequest)

    @POST("auth/me/verify-email")
    suspend fun verifyEmail(@Body body: UserVerifyCodeRequest)

    @POST("auth/me/verify-phone")
    suspend fun verifyPhone(@Body body: UserVerifyCodeRequest)

    @GET("auth/preset-avatars")
    suspend fun getPresetAvatars(): PresetAvatarsResponse

    @POST("auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): RefreshResponse

    @GET("agent-instances/accessible")
    suspend fun getAccessibleAgents(): List<AccessibleAgent>

    @GET("agent-instances/{id}/deployment-status")
    suspend fun getDeploymentStatus(@Path("id") id: String): AgentDeploymentStatus

    @POST("agent-instances/{id}/deploy")
    suspend fun deploy(@Path("id") id: String): AgentDeploymentStatus

    @GET("agent-instances/{id}/models")
    suspend fun getModels(@Path("id") id: String): AgentModelsResponse

    @GET("agent-instances/{id}/files")
    suspend fun listFiles(@Path("id") id: String, @Query("path") path: String): WorkspaceFileList

    @GET("agent-instances/{id}/files/content")
    suspend fun readFile(@Path("id") id: String, @Query("path") path: String): WorkspaceFileContent

    @Streaming
    @GET("agent-instances/{id}/files/download")
    suspend fun downloadFile(@Path("id") id: String, @Query("path") path: String): ResponseBody

    @Multipart
    @POST("agent-instances/{id}/files/upload")
    suspend fun uploadAgentFile(
        @Path("id") id: String,
        @Query("path") path: String,
        @Part file: MultipartBody.Part,
    ): UploadFileResponse

    @POST("agent-instances/{id}/files/mkdir")
    suspend fun createFolder(
        @Path("id") id: String,
        @Query("path") path: String,
        @Body body: CreateFolderRequest,
    )

    @DELETE("agent-instances/{id}/files")
    suspend fun deleteFile(
        @Path("id") id: String,
        @Query("path") path: String,
    )

    @POST("agent-instances/{id}/files/move")
    suspend fun moveFile(
        @Path("id") id: String,
        @Body body: MoveFileRequest,
    )

    @GET("public/app-releases/latest")
    suspend fun getLatestRelease(): AppReleaseLatest?

    @GET("public/app-releases/by-version/{version}")
    suspend fun getReleaseByVersion(@Path("version") version: String): AppReleaseLatest?

    // ── 消息级反馈 / 收藏 ──

    @PUT("message-feedback")
    suspend fun upsertFeedback(@Body body: FeedbackUpsertRequest): retrofit2.Response<Unit>

    @GET("message-feedback")
    suspend fun listFeedback(@Query("session_id") sessionId: String): List<FeedbackItem>

    @PUT("message-favorites")
    suspend fun upsertFavorite(@Body body: FavoriteUpsertRequest): FavoriteItem

    @HTTP(method = "DELETE", path = "message-favorites", hasBody = true)
    suspend fun deleteFavorite(@Body body: FavoriteDeleteRequest): retrofit2.Response<Unit>

    @GET("message-favorites")
    suspend fun listSessionFavorites(@Query("session_id") sessionId: String): List<FavoriteItem>

    @GET("message-favorites/mine")
    suspend fun listMyFavorites(
        @Query("limit") limit: Int = 50,
        @Query("offset") offset: Int = 0,
    ): List<FavoriteItem>
}
