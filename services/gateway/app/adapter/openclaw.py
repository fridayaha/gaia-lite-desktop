"""OpenClaw engine adapter — OpenAI-compatible protocol.

OpenClaw 原生 OpenAI 兼容，无 body/SSE 转换、无 Hermes 专用会话头。
DNS: engine-openclaw-{short_id}.{ns}.svc.cluster.local:8642（契约 §1）。
"""

from .base import EngineAdapter


class OpenClawAdapter(EngineAdapter):
    engine_type = "OPENCLAW"

    async def build_upstream_url(
        self, agent_id: str, path: str, query: str | None = None
    ) -> str:
        return self._build_url(agent_id, path, query)

    def transform_headers(
        self, raw_headers: dict[str, str], api_server_key: str
    ) -> dict[str, str]:
        headers = self._base_headers(raw_headers, api_server_key)
        # OpenAI 兼容引擎不使用 x-session-id，丢弃避免泄露
        headers.pop("x-session-id", None)
        return headers

    # ── Session / Files（OpenAI 兼容 /v1/sessions）──

    def get_session_create_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, "v1/sessions")

    def get_session_list_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, "v1/sessions")

    def get_session_detail_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, f"v1/sessions/{session_id}")

    def get_session_update_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, f"v1/sessions/{session_id}")

    def get_session_delete_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, f"v1/sessions/{session_id}")

    def get_session_messages_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, f"v1/sessions/{session_id}/messages")

    def get_files_url(self, agent_id: str, query: str | None = None) -> str | None:
        return self._build_url(agent_id, "v1/files", query)
