package com.unionagents.enduser.di

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.unionagents.enduser.net.AuthInterceptor
import com.unionagents.enduser.net.AgentHeaderInterceptor
import com.unionagents.enduser.net.GatewayApi
import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.ServerConfig
import com.unionagents.enduser.net.TokenAuthenticator
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import javax.inject.Named
import javax.inject.Qualifier
import javax.inject.Singleton

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ManagerClient

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class GatewayClient

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class RefreshClient

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ManagerRetrofit

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class GatewayRetrofit

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class SseStreamingClient

/** gateway 系 client 共用超时：SSE 长流 + 引擎冷启动都要求放宽 read/connect。 */
private fun OkHttpClient.Builder.gatewayTimeouts(): OkHttpClient.Builder = apply {
    // Hermes POST /v1/runs 在引擎侧同步执行 LLM 调用后才返回 run_id，
    // 默认 10s readTimeout 会先于响应到达触发 SocketTimeoutException（"timeout"）。
    // SSE 流（GET /v1/runs/{id}/events）也需要更长的读间隔以容忍工具执行期间的静默期。
    // connectTimeout 30s：公网 ingress 偶发抖动；readTimeout 5min：覆盖引擎完整 run；
    // writeTimeout 30s：APK 上传不在此 client（走 manager），无需更大。
    connectTimeout(java.time.Duration.ofSeconds(30))
    readTimeout(java.time.Duration.ofMinutes(5))
    writeTimeout(java.time.Duration.ofSeconds(30))
}

/**
 * SSE 专用 client —— 绝不挂 HttpLoggingInterceptor(Level.BODY)：
 * 该拦截器会 `source.request(Long.MAX_VALUE)` 把整个响应体缓冲到 EOF 才把 response
 * 交给上层，SSE 流会被扣留到 run 结束一次性放行（"几十秒后内容一下全出来"的根因）。
 * 参数用 Interceptor/Authenticator 基类型，便于单测以哑实现构造。
 */
internal fun buildSseOkHttpClient(
    authInterceptor: Interceptor,
    agentHeaderInterceptor: Interceptor,
    authenticator: Authenticator,
): OkHttpClient = OkHttpClient.Builder()
    .gatewayTimeouts()
    .addInterceptor(authInterceptor)
    .addInterceptor(agentHeaderInterceptor)
    .authenticator(authenticator)
    .build()

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    fun provideJson(): Json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        explicitNulls = false
        encodeDefaults = true
    }

    @Provides
    @Singleton
    fun provideLoggingInterceptor(): HttpLoggingInterceptor =
        HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

    /**
     * refresh 专用 client：无 Authenticator，避免循环（TokenAuthenticator 用它发 /auth/refresh）。
     */
    @Provides
    @Singleton
    @RefreshClient
    fun provideRefreshClient(logging: HttpLoggingInterceptor): OkHttpClient =
        OkHttpClient.Builder()
            .addInterceptor(logging)
            .build()

    @Provides
    @Singleton
    fun provideTokenRefreshProvider(impl: com.unionagents.enduser.net.TokenRefresher): com.unionagents.enduser.net.TokenRefreshProvider = impl

    @Provides
    @Singleton
    @ManagerClient
    fun provideManagerClient(
        logging: HttpLoggingInterceptor,
        authInterceptor: AuthInterceptor,
        authenticator: TokenAuthenticator,
    ): OkHttpClient = OkHttpClient.Builder()
        // deploy 端点触发 k8s Pod 创建 + readiness 探测，nginx 层 proxy_read_timeout 已是 300s；
        // OkHttp 默认 10s 在引擎镜像拉取 / 启动慢时会先超时。此处对齐 nginx 5min 上限。
        .connectTimeout(java.time.Duration.ofSeconds(30))
        .readTimeout(java.time.Duration.ofMinutes(5))
        .writeTimeout(java.time.Duration.ofSeconds(30))
        .addInterceptor(authInterceptor)
        .addInterceptor(logging)
        .authenticator(authenticator)
        .build()

    @Provides
    @Singleton
    @SseStreamingClient
    fun provideSseStreamingClient(
        authInterceptor: AuthInterceptor,
        agentHeaderInterceptor: AgentHeaderInterceptor,
        authenticator: TokenAuthenticator,
    ): OkHttpClient = buildSseOkHttpClient(authInterceptor, agentHeaderInterceptor, authenticator)

    @Provides
    @Singleton
    @GatewayClient
    fun provideGatewayClient(
        @SseStreamingClient base: OkHttpClient,
        logging: HttpLoggingInterceptor,
    ): OkHttpClient = base.newBuilder()
        // BODY 日志只对普通 JSON 请求挂；SSE 走上面的专用 client（缓冲根因见 buildSseOkHttpClient）。
        // newBuilder 共享连接池/超时/鉴权拦截器，logging 追加在鉴权之后，顺序与原先一致。
        .addInterceptor(logging)
        .build()

    @Provides
    @Singleton
    @ManagerRetrofit
    fun provideManagerRetrofit(
        @ManagerClient client: OkHttpClient,
        json: Json,
        serverConfig: ServerConfig,
    ): Retrofit = Retrofit.Builder()
        .baseUrl(serverConfig.managerUrl)
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()

    @Provides
    @Singleton
    @GatewayRetrofit
    fun provideGatewayRetrofit(
        @GatewayClient client: OkHttpClient,
        json: Json,
        serverConfig: ServerConfig,
    ): Retrofit = Retrofit.Builder()
        .baseUrl(serverConfig.gatewayUrl)
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()

    @Provides
    @Singleton
    @Named("manager_base_url")
    fun provideManagerBaseUrl(serverConfig: ServerConfig): String = serverConfig.managerUrl

    @Provides
    @Singleton
    fun provideManagerApi(@ManagerRetrofit retrofit: Retrofit): ManagerApi =
        retrofit.create(ManagerApi::class.java)

    @Provides
    @Singleton
    fun provideGatewayApi(@GatewayRetrofit retrofit: Retrofit): GatewayApi =
        retrofit.create(GatewayApi::class.java)
}
