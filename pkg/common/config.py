import os

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "UnionAgents-Manager"
    debug: bool = False
    # 运行环境：dev / prod。prod 下强制要求显式 UA_JWT_SECRET（见 security.assert_production_secrets）
    environment: str = "dev"
    # CORS 白名单（逗号分隔）。默认 "*" 走无凭证通配（dev 零配置）；生产设为显式域名
    cors_origins: str = "*"

    # Database
    database_url: str = "postgresql+asyncpg://unionagents:change-me@localhost:5432/unionagents"
    # 测试专用库（真 DB 集成测试用）。必须与生产库分离——V3 测试夹具 teardown 会
    # DELETE FROM <全表> 清空数据，绝不能指向生产 unionagents 库。conftest 会按需
    # 自动创建该库 + create_all 建表，并强校验库名含 "test" 以防误指生产。
    test_database_url: str = "postgresql+asyncpg://unionagents:change-me@localhost:5432/unionagents_test"

    # DB 连接池（缓解长 IO 期间连接占用；生产可经环境变量 UA_POOL_SIZE 等调大）
    pool_size: int = 20
    pool_max_overflow: int = 40
    pool_timeout: int = 60
    pool_recycle: int = 1800  # 30min，防 PG/中间件 idle 超时断连
    pool_pre_ping: bool = True

    # JWT
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 90

    # MinIO / OSS
    minio_endpoint: str = "http://localhost:9000"
    minio_user: str = "unionagents"
    minio_password: str = "change-me"
    minio_bucket: str = "unionagents-archives"
    minio_public_bucket: str = "unionagents-avatars"
    minio_region: str = ""

    # API Server Key (for engine auth)
    api_server_key: str = "change-me"

    # K8s
    k8s_namespace: str = "unionagents"

    # Controller API 地址 —— controller 已并入 manager，/api/controller/* 由 manager 提供。
    # 此值注入引擎 Pod 的 CONTROLLER_URL 环境变量，供 entrypoint 回调（profiles/register 等）。
    controller_base_url: str = "http://manager:8002"

    # Gateway 服务地址（manager 探活 gateway /health）
    gateway_base_url: str = "http://gateway:8010"

    # Langfuse trace 服务地址（monitoring namespace，可选部署；未部署时 dashboard 显示"未部署"而非"异常"）
    langfuse_base_url: str = "http://langfuse.monitoring:3000"
    # Langfuse 外部可访问地址（浏览器能打开的），用于前端"在 Langfuse 中查看"链接。
    # 为空时 fallback 到 langfuse_base_url。ECS 部署配 NodePort 外网地址。
    langfuse_external_url: str = ""
    langfuse_public_key: str = ""  # 空字符串=未部署，manager 调用前先判空
    langfuse_secret_key: str = ""

    # Prometheus 查询地址（monitoring namespace，manager 跨 ns DNS 访问）
    prometheus_url: str = "http://prometheus.monitoring:9090"
    # Grafana 外部可访问地址（浏览器能打开的），用于前端"在 Grafana 中查看"链接。
    # 为空时前端隐藏外链。ECS 部署配 NodePort 外网地址。
    grafana_external_url: str = ""

    # Loki 内部查询地址（manager 跨 ns DNS 访问，不暴露给前端）。
    # logs/search 端点代理调 /loki/api/v1/query_range。
    loki_internal_url: str = "http://loki.monitoring.svc.cluster.local:3100"

    # Hub 能力中心地址（manager 反代 /api/hub/* 给 admin 前端，注入 X-* 身份头）
    hub_base_url: str = "http://hub:8003"

    # Skill Engine 服务地址（manager 反代 /api/skill-engine/* 给 admin 前端，注入 X-* 身份头）
    skill_engine_base_url: str = "http://skill-engine:8004"

    # PVC (persistent volume claim) for engine data
    pvc_enabled: bool = True
    pvc_storage_class: str = "standard"
    pvc_storage_size: str = "5Gi"
    # SUSPEND 时跳过 tar 备份（PVC 稳定后可开启）
    pvc_skip_backup_on_suspend: bool = False
    # DESTROY 时是否删除 PVC（关闭可保留现场调试）
    pvc_reclaim_on_destroy: bool = True

    # ── 浏览器沙箱（VNC 接管）per-profile browser Pod ──
    # browser-v2 镜像 = kasmweb/chrome:1.18.0 + socat（CDP 代理 sidecar 用）。
    # 纯 kasmweb/chrome 无 socat，python TCP relay 在 hermes 并发下间歇 500，故自建 v2。
    # 已推 ACR VPC 端点；云 k3s 节点直接拉，不走 docker.io/daocloud。
    browser_sidecar_image: str = (
        "crpi-x1lxt7dogr41s0b4-vpc.cn-hangzhou.personal.cr.aliyuncs.com"
        "/unionagents/browser-v2:1.18.0"
    )
    # browser-data PVC（kasm /config，per-profile RWO，存 cookies/登录态）
    browser_pvc_storage_class: str = "local-path"
    browser_pvc_size: str = "2Gi"
    # 空闲回收：browser Pod 空闲超时删 Pod（数据在 PVC），下次用重建
    browser_idle_kill_minutes: int = 15
    # CDP 代理端口（chrome 强制绑 127.0.0.1，需 Pod 内代理暴露 0.0.0.0 给引擎跨 Pod 访问）
    browser_cdp_proxy_port: int = 9222
    browser_cdp_chrome_port: int = 9223
    browser_vnc_port: int = 6901

    # Callback base URL (for IM channel webhooks)
    # 生产环境设为实际域名，如 https://chat.unionagents.com
    # 本地 ngrok 测试设为 ngrok 地址
    # 留空则使用相对路径（兼容旧版，但飞书等平台需要完整 URL）
    callback_base_url: str = ""

    # Idle recycle
    idle_suspend_minutes: int = 30
    idle_destroy_hours: int = 24

    # 每日备份保留天数（daily-{date} 滚动清理；DESTROY 永久 archive 不受此限）
    daily_backup_retain_days: int = 30

    # finalizer 销毁前备份：Pod Terminating 后最长等待备份成功的分钟数，超时强制移除
    # finalizer 放行（避免 exec 持续失败时卡死 Pod），并告警。
    finalizer_backup_timeout_minutes: int = 5
    # reconcile finalizer 的轮询间隔（秒）
    finalizer_reconcile_interval_seconds: int = 10
    # 每日全量备份的触发时刻（UTC 小时，0-23）
    daily_backup_hour: int = 3

    # LiteLLM 模型网关
    # 全系统唯一模型入口，引擎不再直连外部供应商
    litellm_base_url: str = "http://litellm:4000"
    # Manager 管理 API 鉴权用 master key（sk- 开头），需冷备
    litellm_master_key: str = "sk-ua-litellm-master-change-me"
    # 加密上游凭据的 salt key，设后不可改，必须备份
    litellm_salt_key: str = "sk-ua-litellm-salt-change-me"
    # 凭证字段级加密 key（Fernet）。加密 SkillCredential.credentials_encrypted 等敏感字段。
    # prod 必须显式设置（见 security.assert_credential_encryption_key），dev 留空走派生兜底。
    # 设后不可改，必须备份——丢失即无法解密已存凭证。
    credential_encryption_key: str = ""
    # OpenAI 兼容 API Key 的 HMAC-SHA256 签名密钥（agent_instance_api_keys.key_hash 用）。
    # prod 必须显式设置 ≥ 32 字符（见 security.assert_api_key_hmac_secret），dev 留空走默认。
    # 设后不可改——改了所有已签发 Key 立即失效（无法再验证）。
    api_key_hmac_secret: str = ""
    # gateway→manager 服务间信任令牌（env UA_INTERNAL_TOKEN，与 gateway 共用）。
    # gateway 调 manager /files/content 解析工作区图片时带 X-Internal-Token 头，manager 据此
    # 放行（替代 client JWT——sk- API Key 客户端无 JWT）。prod 必须显式设置，dev 留空则该头无效。
    internal_token: str = ""
    # ALL/USER 范围 Agent 的计费兜底 Team
    litellm_default_team_id: str = "default"

    # 花费展示汇率：LiteLLM 原生以 USD 记账，展示侧换算为人民币（运维可调）
    spend_usd_to_cny: float = 7.2

    # 日志级别（DEBUG/INFO/WARNING/ERROR）。manager/gateway 启动时 setup_json_logger 读取。
    # DEBUG 时 uvicorn.access 也输出 DEBUG 级别。
    log_level: str = "INFO"

    # SMTP 邮件发送（告警通知 email 渠道用）。空 host=未配置，邮件渠道返回 ok=False 不抛异常。
    # 凭据通过环境变量 UA_SMTP_HOST / UA_SMTP_PASSWORD 等注入，不入仓库。
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"  # "ssl" / "starttls" / "none"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "UnionAgents 告警"

    # APP 发布（admin 端「APP 管理」页 publish 触发 ApkPatcher.patch）
    # dev 本地留空走兜底；prod 必须显式配置（keystore + 密码通过 k8s Secret 注入）。
    # keystore 文件路径：prod 由 initContainer 从 Secret base64 解码后落到此路径。
    apk_keystore_path: str = "/keystore/release.keystore"
    apk_keystore_alias: str = "unionagents"
    apk_keystore_password: str = ""
    apk_key_password: str = ""
    # zipalign + apksigner 二进制路径，默认在 PATH 里（manager 镜像装 Android build-tools）
    apk_zipalign_bin: str = "zipalign"
    apk_apksigner_bin: str = "apksigner"
    # base APK 捆绑目录（manager 镜像 COPY base-apks/ → /app/base-apks/）
    apk_base_dir: str = "/app/base-apks"

    @model_validator(mode="after")
    def _assert_pvc_backup_consistency(self):
        # pvc_skip_backup_on_suspend=True 表示 SUSPEND 时不做 MinIO 备份、数据只活 PVC 上。
        # 若同时 pvc_reclaim_on_destroy=True，DESTROY 会删 PVC 且无归档 → 必然永久丢数据。
        # 启动期拒绝该致命组合；运行期 _do_destroy 也会再次拒删兜底。
        if self.pvc_skip_backup_on_suspend and self.pvc_reclaim_on_destroy:
            raise ValueError(
                "配置冲突：pvc_skip_backup_on_suspend=True 必须搭配 "
                "pvc_reclaim_on_destroy=False（否则 DESTROY 删 PVC 无归档 = 数据丢失）"
            )
        return self

    class Config:
        env_file = ".env"
        env_prefix = "UA_"


settings = Settings()


# =========================================
# 引擎运行参数（V3 三层模型）
# =========================================
# 引擎类型是「低频强契约」——新增一种引擎必须改代码（枚举、gateway proxy 分支、
# controller 对该引擎原生 API 的适配、Profile 命名规则）。它不是数据驱动可插拔的，
# 因此作为系统配置项而非独立数据表。镜像版本可由环境变量热更新，无需改代码。
#   UA_ENGINE_IMAGE     全局覆盖所有引擎镜像
#   UA_<CODE>_IMAGE     按引擎覆盖，如 UA_HERMES_IMAGE
ENGINE_RUNTIMES: dict[str, dict] = {
    "HERMES":   {"image": "unionagents/engine-hermes-v2:latest", "port": 8642},
    "OPENCLAW": {"image": "unionagents/engine-openclaw:latest", "port": 8642},
    "DIFY":     {"image": "unionagents/engine-dify:latest", "port": 8080},
}


def get_engine_runtime(engine_type: str | None) -> dict:
    """按 engine_type 返回 {"image", "port"}，镜像可被环境变量覆盖。

    controller 创建 Pod 时调用，替代原有硬编码 8642 与 os.getenv("UA_ENGINE_IMAGE")。
    """
    code = (engine_type or "HERMES").upper()
    rt = dict(ENGINE_RUNTIMES.get(code, ENGINE_RUNTIMES["HERMES"]))
    rt["image"] = (
        os.getenv(f"UA_{code}_IMAGE")
        or os.getenv("UA_ENGINE_IMAGE")
        or rt["image"]
    )
    return rt
