"""Hermes engine adapter — OpenAI-compatible protocol.

Hermes 原生 OpenAI 兼容，无需 body/SSE 转换。
DNS: engine-hermes-{short_id}.{ns}.svc.cluster.local:8642（契约 §1）。

会话头翻译：通用 ``X-Session-Id`` → Hermes 专用 ``X-Hermes-Session-Id``。

路径映射：Hermes 引擎的会话 REST API 挂在 ``/api/sessions``（非 ``/v1/sessions``），
统一 ``v1/sessions`` 需映射为 ``api/sessions`` 否则引擎返 404。
"""

from .base import EngineAdapter


class HermesAdapter(EngineAdapter):
    engine_type = "HERMES"

    async def build_upstream_url(
        self, agent_id: str, path: str, query: str | None = None
    ) -> str:
        return self._build_url(agent_id, path, query)

    def map_path(self, path: str) -> str:
        """统一路径 → Hermes 原生路径。

        Hermes 会话 REST 在 ``/api/sessions``（非 ``/v1/sessions``）。
        其他路径（``v1/chat/completions``、``v1/models``、``v1/runs`` 等）原生即用。
        """
        if path == "v1/sessions":
            return "api/sessions"
        if path.startswith("v1/sessions/"):
            rest = path.removeprefix("v1/sessions/")
            return f"api/sessions/{rest}"
        return path

    def transform_headers(
        self, raw_headers: dict[str, str], api_server_key: str
    ) -> dict[str, str]:
        headers = self._base_headers(raw_headers, api_server_key)
        # 通用 X-Session-Id → Hermes 专用 X-Hermes-Session-Id
        session_id = headers.pop("x-session-id", "")
        if session_id:
            headers["x-hermes-session-id"] = session_id
        return headers

    # ── Session / Files（Hermes 托管 /api/sessions）──

    def get_session_create_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path("v1/sessions"))

    def get_session_list_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path("v1/sessions"))

    def get_session_detail_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_update_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_delete_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_messages_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}/messages"))

    def get_files_url(self, agent_id: str, query: str | None = None) -> str | None:
        return self._build_url(agent_id, "v1/files", query)
