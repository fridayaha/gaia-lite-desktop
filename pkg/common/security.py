"""共享安全工具：CORS 白名单解析 + 生产环境密钥 fail-fast 校验。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 默认 JWT 密钥（仅用于 dev 环境）。生产环境必须通过环境变量覆盖。
DEFAULT_JWT_SECRET = "change-me"


def parse_cors_origins(raw: str) -> list[str]:
    """逗号分隔的 origin 字符串 → 去空白后的列表。

    例: "https://admin.a.com, https://chat.a.com" → ["https://admin.a.com", "https://chat.a.com"]
    """
    return [o.strip() for o in (raw or "").split(",") if o.strip()]


def configure_cors(app: FastAPI, origins: list[str]) -> None:
    """按白名单挂 CORS 中间件。

    - 显式白名单（不含 ``*``）：``allow_credentials=True``，仅允许列表内 origin —— 生产推荐。
    - 通配 ``*``：``allow_credentials=False``（符合 CORS 规范；本系统鉴权走 Authorization
      头而非 Cookie，无需凭证）—— dev 默认零配置。
    - 空白名单：不挂中间件（fail-closed）。

    修复要点：原先 ``allow_origins=["*"]`` + ``allow_credentials=True`` 会被 Starlette
    反射任意 Origin，等于任意跨站可携带凭证访问 API。此处按规范处理。
    """
    if not origins:
        return
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def assert_production_secrets(jwt_secret: str | None, environment: str) -> None:
    """非 dev 环境下，jwt_secret 未设置或仍为默认值则拒绝启动（fail-fast）。

    防止生产环境因漏配环境变量而静默使用弱默认密钥，导致 JWT 可被伪造。
    """
    if environment == "dev":
        return
    if not jwt_secret or jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            f"UA_JWT_SECRET 必须在非 dev 环境显式设置为非默认值"
            f"（当前 environment={environment!r}）。"
            f"dev 环境可保留默认值或显式设置 UA_ENVIRONMENT=dev。"
        )


def assert_credential_encryption_key(key: str | None, environment: str) -> None:
    """非 dev 环境下，credential_encryption_key 未设置或为空则拒绝启动（fail-fast）。

    防止生产环境凭证加密 key 缺失而静默回退到弱派生兜底 key，导致已存凭证可被解密。
    dev 环境留空时 crypto._load_fernet 用固定 material 派生兜底 key。
    """
    if environment == "dev":
        return
    if not key:
        raise RuntimeError(
            "UA_CREDENTIAL_ENCRYPTION_KEY 必须在非 dev 环境显式设置"
            f"（当前 environment={environment!r}）。"
        )


def assert_api_key_hmac_secret(secret: str | None, environment: str) -> None:
    """非 dev 环境下，api_key_hmac_secret 未设置或 < 32 字符则拒绝启动（fail-fast）。

    防止生产环境 OpenAI 兼容 API Key 签名密钥缺失或过短，导致已签发 Key 可被伪造。
    dev 环境留空时 api_key_service/gateway api_key_auth 用固定默认值派生（仅供本地测试）。
    """
    if environment == "dev":
        return
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "UA_API_KEY_HMAC_SECRET 必须在非 dev 环境显式设置且不少于 32 字符"
            f"（当前 environment={environment!r}）。"
        )
