"""Gateway configuration — Pydantic Settings backed by env vars."""
from pydantic_settings import BaseSettings


class GatewaySettings(BaseSettings):
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    api_server_key: str = "change-me"
    k8s_namespace: str = "unionagents"
    engine_port: int = 8642
    # 浏览器沙箱 VNC：kasm browser Pod 的 VNC web 端口（自签 WSS，gateway 桥接上游）
    browser_vnc_port: int = 6901
    # controller 已并入 manager：/api/controller/* 由 manager:8002 提供
    controller_url: str = "http://manager:8002"
    # 运行环境：dev / prod。prod 下强制要求显式 UA_JWT_SECRET（见 security.assert_production_secrets）
    environment: str = "dev"
    # CORS 白名单（逗号分隔）。默认 "*" 走无凭证通配（dev 零配置）；生产设为显式域名
    cors_origins: str = "*"
    # SSE 静默看门狗：转发引擎 SSE 时超过 N 秒无任何字节则注入 gateway.silence
    # 提示帧（端上可显示"正在生成较多内容…"）。覆盖引擎生成长工具调用参数等
    # 无事件时段；0 关闭。
    sse_silence_hint_seconds: float = 8.0
    # 语音 ASR sidecar 地址（asr-sidecar 同 pod，faster-whisper）。未配置则 voice 走兜底提示
    asr_url: str = "http://localhost:9100"
    # === ASR provider 抽象（外部 ASR 优先，local 走旧 sidecar）===
    # ASR 供应商：volcengine / local / aliyun / tencent / huawei；空则不识别语音（兜底提示）
    asr_provider: str = ""
    asr_timeout: float = 30.0
    # 火山引擎 OpenSpeech 豆包 ASR（volc.seedasr.auc 录音文件大模型）
    # X-Api-Key 单字段鉴权（BytePlus 国际版，不需 App-Key），submit+query 异步
    asr_volc_api_key: str = ""
    asr_volc_resource_id: str = "volc.seedasr.auc"
    asr_volc_endpoint: str = ""  # 留空用默认 openspeech.bytedance.com
    # 阿里云（待实现）
    asr_aliyun_app_key: str = ""
    asr_aliyun_access_key: str = ""
    asr_aliyun_access_secret: str = ""
    asr_aliyun_endpoint: str = ""
    # 腾讯云（待实现）
    asr_tencent_secret_id: str = ""
    asr_tencent_secret_key: str = ""
    asr_tencent_app_id: str = ""
    # 华为云（待实现）
    asr_huawei_ak: str = ""
    asr_huawei_sk: str = ""
    asr_huawei_endpoint: str = ""
    # 企微 AI Bot openws 地址（wecom channel_type WS 桥接用）
    wecom_openws_url: str = "wss://openws.work.weixin.qq.com"
    # 日志级别（DEBUG/INFO/WARNING/ERROR）
    log_level: str = "INFO"
    # 会话重置自助命令（逗号分隔）；用户在 IM 发送任一命令 → 删引擎 session 重置对话。
    # trim+小写后精确匹配，不做包含/前缀匹配以避免误触。env UA_SESSION_RESET_COMMANDS
    session_reset_commands: str = "/重置会话,/reset,/清空会话"
    # OpenAI 兼容 API Key 的 HMAC-SHA256 签名密钥（与 manager 共用，env UA_API_KEY_HMAC_SECRET）
    # prod 必须显式设置 ≥ 32 字符，dev 留空走默认
    api_key_hmac_secret: str = ""
    # gateway→manager 服务间信任令牌（与 manager 共用，env UA_INTERNAL_TOKEN）。
    # gateway 调 manager /files/content 解析工作区图片时用 X-Internal-Token 头替代转发 client
    # token（后者对 sk- API Key 客户端会 401）。prod 必须显式设置，dev 留空则不带该头。
    internal_token: str = ""
    # Redis 共享存储（wecom_bot_callback 流式状态跨副本共享）。空则降级内存模式（单副本/本地冒烟）。
    # 复用 monitoring ns langfuse Redis 时用独立 DB index 隔离，如 redis://:pwd@redis.monitoring:6379/2
    redis_url: str = ""
    # 当前时间 ephemeral system 注入（修正 hermes 跨天会话 system prompt 日期固化）。
    # 每轮转发前注入「当前时间」作为 ephemeral system，叠加在 core 之上不覆盖、不持久化，
    # core prefix cache 不受影响。env UA_INJECT_CURRENT_TIME=0 关闭。
    inject_current_time: bool = True
    # 默认时区（对齐 hermes 容器 TZ=Asia/Shanghai；per-agent runtime_config.timezone 缺省时回落）
    default_timezone: str = "Asia/Shanghai"
    # 当前用户身份 ephemeral system 注入（与时间注入同机制）。
    # resolve() 内按 profile_name 调 manager /user-context 端点拉用户身份（60s 缓存），
    # 每轮转发前注入「当前用户身份」（角色/用户组/业务用户名，非 PII）作 ephemeral system。
    # 只注非个人 PII，避免进 langfuse trace；手机号/邮箱等强 PII 由 current-user-info skill 按需查。
    # env UA_INJECT_USER_CONTEXT=0 关闭。
    inject_user_context: bool = True

    class Config:
        env_prefix = "UA_"


settings = GatewaySettings()
