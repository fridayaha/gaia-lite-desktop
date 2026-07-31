"""Abstract base class for engine protocol adapters.

Each engine type (Hermes / Dify / OpenClaw) gets its own adapter that knows how
to build upstream URLs, transform headers, and translate between the unified
OpenAI-compatible format and the engine's native API.

设计约束（见 docs/merge/01-接口契约.md §1、§4）：
- Gateway 反向依赖禁止：adapter **不得**查询 manager/controller 获取 upstream
  地址，仅靠 ``X-Agent-ID`` + DNS 命名规范构造 URL：
    engine-{engine_type}-{instance_id[:8]}.{namespace}.svc.cluster.local:{port}
- ``transform_headers`` 必须去掉 ``Origin`` / ``Referer``（Hermes 收 Origin 返 403）。
"""

from abc import ABC, abstractmethod
from typing import Any

# engine_type → 默认端口（契约 §1）
ENGINE_PORTS: dict[str, int] = {
    "hermes": 8642,
    "openclaw": 8642,
    "dify": 8080,
}

# Hop-by-hop 头 + 客户端不应控制的安全头：
# - origin/referer：引擎收 Origin 返 403（Chrome SSE Failed to fetch 主因）
# - x-hermes-profile：服务端计算，忽略客户端传入值
# - x-client-type：gateway 内部用于 Langfuse channel_type 派生，不透传引擎
_HOP_BY_HOP = frozenset({
    "host", "content-length", "content-encoding",
    "transfer-encoding", "connection",
})
_STRIP_HEADERS = _HOP_BY_HOP | {"origin", "referer", "x-hermes-profile", "x-client-type"}


def build_engine_dns(engine_type: str, agent_id: str, namespace: str) -> str:
    """按 DNS 命名规范构造 engine Service 主机:端口（engine_type 驱动）。

    short_id = agent_id.replace("-", "")[:8]  （与 Repo1 DifyAdapter._build_url 一致）
    """
    short_id = str(agent_id).replace("-", "")[:8]
    et = engine_type.lower()
    port = ENGINE_PORTS.get(et, 8642)
    return f"engine-{et}-{short_id}.{namespace}.svc.cluster.local:{port}"


class EngineAdapter(ABC):
    """引擎协议适配器抽象基类。

    子类需实现：``engine_type`` / ``build_upstream_url`` / ``transform_headers``。
    其余方法按引擎能力覆盖（默认 identity 或 None）。
    """

    def __init__(self, k8s_namespace: str = "unionagents") -> None:
        self.namespace = k8s_namespace
        # 由 proxy 注入：当前请求的 Langfuse trace 对象，用于 adapter 在 SSE 流里
        # 为引擎原生事件（如 Dify agent_thought）创建 SPAN observation。None=未启用。
        self._langfuse_trace: Any = None

    @property
    @abstractmethod
    def engine_type(self) -> str:
        """引擎类型标识（如 'HERMES' / 'DIFY' / 'OPENCLAW'）。"""
        ...

    # ── URL ──────────────────────────────────────────────────

    def _dns(self, agent_id: str) -> str:
        """构造 engine Service 的 host:port（engine_type 驱动）。"""
        return build_engine_dns(self.engine_type, agent_id, self.namespace)

    @abstractmethod
    async def build_upstream_url(
        self, agent_id: str, path: str, query: str | None = None
    ) -> str:
        """构造 upstream URL。``path`` 应为已 ``map_path`` 后的引擎路径。"""
        ...

    def _build_url(self, agent_id: str, path: str, query: str | None = None) -> str:
        """同步构造完整 upstream URL（无 IO，供 session/files 钩子复用）。"""
        url = f"http://{self._dns(agent_id)}"
        if path:
            url += f"/{path}"
        if query:
            url += f"?{query}"
        return url

    def map_path(self, path: str) -> str:
        """统一路径 → 引擎路径映射（默认 identity；Dify 覆盖）。"""
        return path

    # ── Headers ───────────────────────────────────────────────

    def _base_headers(
        self, raw_headers: dict[str, str], api_server_key: str
    ) -> dict[str, str]:
        """通用头处理：去 hop-by-hop + Origin/Referer/x-hermes-profile，
        注入引擎 API key，弹出 X-Engine-Type（不再透传给引擎）。"""
        headers = {
            k: v for k, v in raw_headers.items()
            if k not in _STRIP_HEADERS
        }
        headers["authorization"] = f"Bearer {api_server_key}"
        headers.pop("x-engine-type", None)
        return headers

    @abstractmethod
    def transform_headers(
        self, raw_headers: dict[str, str], api_server_key: str
    ) -> dict[str, str]:
        """转换请求头为引擎特定头（在 ``_base_headers`` 基础上做会话头翻译等）。"""
        ...

    # ── Body transform ────────────────────────────────────────

    def transform_request_body(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> bytes:
        """请求体转换（OpenAI → 引擎原生格式）。默认 identity。"""
        return body

    def transform_response_body(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> bytes:
        """响应体转换（引擎 SSE → OpenAI SSE）。默认 identity（透传不修改）。"""
        return body

    # ── SSE 流式转换 ──────────────────────────────────────────

    def is_sse_transformable(self, path: str) -> bool:
        """该路径的 SSE 响应是否需要流式协议转换（默认 False = 透传不修改）。

        返回 True 时，proxy 会调用 ``transform_sse_stream`` 按 event 边界转换；
        返回 False 时直接透传字节（Hermes/OpenClaw OpenAI 兼容，无需转换）。
        """
        return False

    async def transform_sse_stream(
        self, path: str, byte_iter, headers: dict[str, str]
    ):
        """流式转换 SSE 字节迭代器。默认透传。Dify 覆盖按 event 转换。"""
        async for chunk in byte_iter:
            yield chunk

    # ── Session / Files URL 钩子（引擎托管；返回 None = 无引擎 API）──

    def get_session_create_url(self, agent_id: str) -> str | None:
        return None

    def get_session_list_url(self, agent_id: str) -> str | None:
        return None

    def get_session_detail_url(self, agent_id: str, session_id: str) -> str | None:
        return None

    def get_session_update_url(self, agent_id: str, session_id: str) -> str | None:
        return None

    def get_session_delete_url(self, agent_id: str, session_id: str) -> str | None:
        return None

    def get_session_messages_url(self, agent_id: str, session_id: str) -> str | None:
        return None

    def get_files_url(self, agent_id: str, query: str | None = None) -> str | None:
        return None
