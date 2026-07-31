"""Dify Console API client — 集中封装 Dify 平台管理接口调用。

Dify 没有正式的"开放管理 API"，所有管理能力封装在 Console API（`/console/api/*`）里，
给 Dify 自带网页前端用，无正式文档但源码可查。本模块集中处理：
  - 账号密码换 JWT access_token（POST /console/api/login）
  - 列出工作区应用（GET /console/api/apps）
  - 获取应用详情（GET /console/api/apps/{app_id}）—— 含 model_config（provider/model）
  - 获取应用 API Key（GET /console/api/apps/{id}/api-keys）
  - 创建应用 API Key（POST /console/api/apps/{id}/api-keys）
  - 获取 model provider credentials（GET /workspaces/current/model-providers/{provider}/credentials）
    —— 含 api_base 等 provider 配置，用于判断 Dify 应用 LLM 是否走我们网关

Dify 大版本升级可能改 Console API，集中在这一处模块，升级只改这里。
启动时调用方应做一次 login 探活，失败给运维预警。

参考源码（Dify 1.x）：
  - api/controllers/console/auth/login.py（LoginApi，返回 {"result": "success"} + Set-Cookie）
  - api/libs/token.py（set_access_token_to_cookie，cookie name = "access_token"）
  - api/libs/encryption.py（FieldEncryption.decrypt_field，password 用 Base64 编码）
  - api/controllers/console/app/app.py（AppListApi / AppApi）
  - api/controllers/console/app/model_config.py（ModelConfigResource，仅 POST update）
  - api/controllers/console/workspace/model_providers.py（ModelProviderCredentialApi）
  - api/controllers/console/apikey.py（AppApiKeyListResource）

Dify 1.x 鉴权变化：
  - Login 响应体只返回 {"result": "success"}，access_token 通过 Set-Cookie 下发
  - 后续请求自动带 cookie，或读 cookie 后用 Authorization: Bearer 头
  - 客户端必须用持久化 cookie jar 的 httpx.AsyncClient，不能每次请求新建 client
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class DifyConsoleError(Exception):
    """Dify Console API 调用异常。"""

    def __init__(self, message: str, status_code: int | None = None, *, is_auth_error: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.is_auth_error = is_auth_error


# Dify mode → 我们 app_type 的映射（参考 plan）
_MODE_TO_APP_TYPE: dict[str, str] = {
    "chat": "chat",
    "agent-chat": "agent",
    "advanced-chat": "workflow",
    "workflow": "workflow",
    # completion 不支持，由调用方过滤
}


def map_dify_mode_to_app_type(mode: str | None) -> str | None:
    """Dify 应用 mode → 我们 app_type。不支持的 mode（如 completion）返回 None。"""
    if not mode:
        return None
    return _MODE_TO_APP_TYPE.get(mode)


# Dify JWT 默认 30 天过期；提前 5 分钟刷新，避免边界失效
_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)
_HTTP_TIMEOUT = 10.0  # 秒
# Dify 1.x cookie name（libs/token.py set_access_token_to_cookie / set_csrf_token_to_cookie）
_COOKIE_ACCESS_TOKEN = "access_token"
_COOKIE_CSRF_TOKEN = "csrf_token"
# Dify 1.x 要求 header X-CSRF-Token == cookie csrf_token（libs/token.py check_csrf_token）
_HEADER_CSRF_TOKEN = "X-CSRF-Token"


class DifyConsoleClient:
    """Dify Console API 同步封装（cookie-based session，过期自动重新登录）。

    Usage:
        client = DifyConsoleClient(base_url, email, password)
        token = await client.login()  # 首次登录，外部缓存
        # 后续调用前传缓存的 token + 过期时间
        await client.ensure_token(cached_token=cached_token, cached_expires_at=expires_at)
        apps = await client.list_apps()
        await client.close()  # 用完关闭持久化 http client
    """

    def __init__(self, base_url: str, email: str, password: str):
        if not base_url:
            raise ValueError("base_url 不能为空")
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        # 内存态 token（持久化由调用方处理；cookie jar 自动管理 session）
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._csrf_token: str | None = None
        self._lock = asyncio.Lock()
        # 持久化 http client：cookie jar 跨请求共享，Dify 1.x login 后 cookie 自动带
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """懒初始化持久化 http client（cookie jar 跨请求共享）。"""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        return self._http

    async def login(self) -> tuple[str, datetime]:
        """账号密码换 access_token。返回 (token, expires_at)。

        Dify 1.x：响应体只返回 {"result": "success"}，access_token 在 Set-Cookie 里。
        本方法从 cookie jar 提取 token 供调用方持久化。

        Raises DifyConsoleError: 登录失败（401/网络/JSON 异常）
        """
        http = self._get_http()
        try:
            r = await http.post(
                f"{self.base_url}/console/api/login",
                json={
                    "email": self.email,
                    # Dify 1.x 用 Base64 编码 password 传输（不是加密，只是 obfuscation）
                    # 参考 /app/api/libs/encryption.py FieldEncryption.decrypt_field
                    "password": base64.b64encode(self.password.encode("utf-8")).decode("ascii"),
                    "remember_me": True,
                    "language": "zh-Hans",
                },
            )
        except httpx.HTTPError as e:
            raise DifyConsoleError(f"登录 Dify 失败：网络错误 {e}") from e

        if r.status_code != 200:
            is_auth = r.status_code == 401 or r.status_code == 403
            detail = _safe_extract_detail(r)
            raise DifyConsoleError(
                f"登录 Dify 失败：HTTP {r.status_code} {detail}",
                status_code=r.status_code,
                is_auth_error=is_auth,
            )

        # Dify 1.x: 响应体是 {"result": "success"}，token 在 cookie 里
        # 兼容老版本：响应体可能直接给 {data: {access_token}} 或扁平 {access_token}
        token: str | None = None
        try:
            data = r.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            token = inner.get("access_token") if isinstance(inner, dict) else None

        if not token:
            # 从 cookie jar 提取（Dify 1.x 主路径）
            token = r.cookies.get(_COOKIE_ACCESS_TOKEN) or http.cookies.get(_COOKIE_ACCESS_TOKEN)

        if not token:
            raise DifyConsoleError(f"登录响应缺少 access_token（body+cookie 均无）：{data!r} cookies={dict(r.cookies)}")

        # remember_me=True 时 Dify 默认 30 天过期
        expires_at = datetime.now(timezone.utc) + timedelta(days=29)
        self._token = token
        self._expires_at = expires_at
        # Dify 1.x: login 同时下发 csrf_token cookie，后续请求需 X-CSRF-Token 头匹配
        self._csrf_token = (
            r.cookies.get(_COOKIE_CSRF_TOKEN) or http.cookies.get(_COOKIE_CSRF_TOKEN)
        )
        return token, expires_at

    async def ensure_token(
        self,
        cached_token: str | None = None,
        cached_expires_at: datetime | None = None,
    ) -> str:
        """确保有有效 token：优先用缓存，过期前 5min 重新登录。

        Args:
            cached_token: 调用方持久化的 token（如 EngineConfig.cached_access_token）
            cached_expires_at: 调用方持久化的过期时间
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            # 1. 优先用持久化缓存
            if cached_token and cached_expires_at:
                if cached_expires_at - now > _TOKEN_REFRESH_BUFFER:
                    self._token = cached_token
                    self._expires_at = cached_expires_at
                    return cached_token
            # 2. 内存缓存仍有效
            if self._token and self._expires_at and self._expires_at - now > _TOKEN_REFRESH_BUFFER:
                return self._token
            # 3. 重新登录
            token, _ = await self.login()
            return token

    async def list_apps(self) -> list[dict[str, Any]]:
        """列出工作区所有应用。过滤掉 completion 模式。

        Returns: [{id, name, mode, description, ...}]
        """
        token = await self.ensure_token(self._token, self._expires_at)
        apps: list[dict[str, Any]] = []
        page = 1
        # 分页拉取，limit=100 一次拉完（Dify 工作区应用一般 < 100）
        while True:
            r = await _do_request_with_retry(
                self,
                "GET",
                f"{self.base_url}/console/api/apps",
                params={"page": page, "limit": 100},
                token=token,
            )
            data = r.json()
            items = data.get("data") or []
            apps.extend(items)
            if not data.get("has_more"):
                break
            page += 1
            if page > 50:  # 安全上限
                break
        # 过滤掉 completion（我们 app_type 不支持）
        return [a for a in apps if map_dify_mode_to_app_type(a.get("mode")) is not None]

    async def get_app_detail(self, app_id: str) -> dict[str, Any]:
        """获取应用详情。

        Dify Console API: GET /console/api/apps/{app_id}
        返回 AppDetailWithSite schema（name/description/icon/site 等）。
        model_config（provider/model/completion_params）在 AppModelConfig 关联表里，
        是否 dump 进 app 详情依 Dify 版本而定，需调用方自行检查返回里有无 model_config 字段。

        Returns: 完整响应 dict
        """
        token = await self.ensure_token(self._token, self._expires_at)
        r = await _do_request_with_retry(
            self,
            "GET",
            f"{self.base_url}/console/api/apps/{app_id}",
            token=token,
        )
        return r.json()

    async def get_provider_credentials(self, provider: str) -> dict[str, Any]:
        """获取工作区 model provider 的 credentials 配置。

        Dify Console API: GET /console/api/workspaces/current/model-providers/{provider}/credentials
        返回 {credentials: {...}}，credentials 含 api_base、api_key（可能 mask）等字段。

        用于判断 Dify 应用 LLM 是否走我们网关：拿到 app 的 model.provider 后，
        调本方法拿该 provider 的 api_base，比对我们网关域名。

        Args:
            provider: provider 路径，可含 "/"，如 "langgenius/openai/openai" 或 "openai"
        Returns: credentials dict（不含外层 {credentials: ...} 包装）
        """
        token = await self.ensure_token(self._token, self._expires_at)
        r = await _do_request_with_retry(
            self,
            "GET",
            f"{self.base_url}/console/api/workspaces/current/model-providers/{provider}/credentials",
            token=token,
        )
        data = r.json()
        if isinstance(data, dict) and "credentials" in data:
            return data["credentials"]
        return data

    async def get_app_api_keys(self, app_id: str) -> list[dict[str, Any]]:
        """列出应用已有 API Key。

        Returns: [{id, token, last_used_at, created_at}]
        """
        token = await self.ensure_token(self._token, self._expires_at)
        r = await _do_request_with_retry(
            self,
            "GET",
            f"{self.base_url}/console/api/apps/{app_id}/api-keys",
            token=token,
        )
        data = r.json()
        # Dify 返回 {data: [...]}
        return data.get("data") or data if isinstance(data, dict) else data

    async def create_app_api_key(self, app_id: str) -> dict[str, Any]:
        """为应用创建新 API Key。"""
        token = await self.ensure_token(self._token, self._expires_at)
        r = await _do_request_with_retry(
            self,
            "POST",
            f"{self.base_url}/console/api/apps/{app_id}/api-keys",
            token=token,
        )
        return r.json()

    async def get_app_token_costs(
        self,
        app_id: str,
        mode: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """获取应用每日 token + cost 统计（Dify plugin 已用 YAML pricing 算好 USD）。

        Dify Console API:
          - message/agent/chat 模式: GET /console/api/apps/{app_id}/statistics/token-costs
            返回 [{date, token_count, total_price, currency}]
          - workflow 模式: GET /console/api/apps/{app_id}/workflow/statistics/token-costs
            返回 [{date, token_count}]（Dify 1.14.2 workflow_runs 不存 cost，total_price 缺失）

        Args:
            app_id: Dify 应用 ID
            mode: 应用模式（chat/agent-chat/advanced-chat/workflow/completion 等）
            start/end: 时间范围，格式 "%Y-%m-%d %H:%M"（Dify 要求带时分）

        Returns:
            list of {date: "YYYY-MM-DD", token_count: int|str, total_price: str|None, currency: str|None}
            workflow 模式 total_price/currency 为 None。
        """
        token = await self.ensure_token(self._token, self._expires_at)
        path = "/workflow/statistics/token-costs" if mode == "workflow" else "/statistics/token-costs"
        r = await _do_request_with_retry(
            self,
            "GET",
            f"{self.base_url}/console/api/apps/{app_id}{path}",
            params={"start": start, "end": end},
            token=token,
        )
        data = r.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"] or []
        return data if isinstance(data, list) else []

    async def close(self) -> None:
        """关闭持久化 http client + 清理内存态 token。"""
        self._token = None
        self._expires_at = None
        self._csrf_token = None
        if self._http and not self._http.is_closed:
            await self._http.aclose()
        self._http = None


async def _do_request_with_retry(
    client: DifyConsoleClient,
    method: str,
    url: str,
    *,
    token: str,
    params: dict | None = None,
    json_body: dict | None = None,
) -> httpx.Response:
    """发起请求，401 时自动重新登录一次重试。

    Dify 1.x：cookie jar 自动带 access_token cookie，同时额外发 Bearer 头兼容老版本。

    Raises DifyConsoleError: 网络/非 2xx 错误
    """
    http = client._get_http()
    headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
    # Dify 1.x: X-CSRF-Token 头必须等于 cookie csrf_token，否则 401
    if client._csrf_token:
        headers[_HEADER_CSRF_TOKEN] = client._csrf_token
    try:
        r = await http.request(method, url, params=params, json=json_body, headers=headers)
    except httpx.HTTPError as e:
        raise DifyConsoleError(f"调用 Dify 失败：网络错误 {e}") from e

    if r.status_code == 401:
        # token/cookie 失效，重新登录后重试一次
        new_token, _ = await client.login()
        headers = {"Authorization": f"Bearer {new_token}"}
        if client._csrf_token:
            headers[_HEADER_CSRF_TOKEN] = client._csrf_token
        try:
            r = await http.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.HTTPError as e:
            raise DifyConsoleError(f"重试调用 Dify 失败：网络错误 {e}") from e

    if r.status_code >= 400:
        is_auth = r.status_code in (401, 403)
        detail = _safe_extract_detail(r)
        raise DifyConsoleError(
            f"调用 Dify 失败：HTTP {r.status_code} {detail}",
            status_code=r.status_code,
            is_auth_error=is_auth,
        )

    return r


def _safe_extract_detail(r: httpx.Response) -> str:
    """从 Dify 错误响应里尽量提取可读信息。"""
    try:
        data = r.json()
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error") or data.get("detail")
            if msg:
                return str(msg)
            return str(data)
    except Exception:
        pass
    return r.text[:200] if r.text else ""
