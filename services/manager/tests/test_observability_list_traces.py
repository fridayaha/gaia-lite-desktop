"""list_traces endpoint 单元测试。

验证 ``GET /api/manager/observability/traces`` 排除 Hermes 内部 trace
（``name == "Hermes turn"``）的过滤行为。Hermes langfuse 插件写的内部
trace 通过 trace 详情页 hermes-correlation 端点关联展示，不应出现在链路
追踪列表里。
"""

from unittest.mock import AsyncMock

import pytest


def _trace(
    *,
    trace_id: str,
    name: str,
    session_id: str = "sess-1",
    user_id: str = "agent-1",
) -> dict:
    """构造一个 Langfuse v3 trace 对象。"""
    return {
        "id": trace_id,
        "name": name,
        "sessionId": session_id,
        "userId": user_id,
        "timestamp": "2026-07-17T03:43:58.524Z",
        "metadata": {"enduser_id": "eu-1", "channel_type": "web"},
        "observationCount": 1,
    }


def _list_traces_response(traces: list[dict]) -> dict:
    """构造 Langfuse v3 list_traces 响应 {data, meta}。"""
    return {
        "data": traces,
        "meta": {
            "totalItems": len(traces),
            "totalPages": 1,
            "page": 1,
            "limit": 50,
        },
    }


@pytest.mark.asyncio
async def test_list_traces_filters_out_hermes_turn(client, monkeypatch):
    """链路追踪列表排除 Hermes 内部 trace（name == 'Hermes turn'）。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([
            _trace(trace_id="gw-1", name="chat_completion"),
            _trace(trace_id="hermes-1", name="Hermes turn"),
            _trace(trace_id="gw-2", name="run"),
            _trace(trace_id="hermes-2", name="Hermes turn"),
        ])),
    )
    # list_observations 也要 mock，避免端点拉 observation 失败
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),
    )

    resp = await client.get("/api/manager/observability/traces")
    assert resp.status_code == 200
    body = resp.json()
    ids = [it["id"] for it in body["items"]]
    assert ids == ["gw-1", "gw-2"]
    # Hermes turn 不在结果里
    assert "hermes-1" not in ids
    assert "hermes-2" not in ids


@pytest.mark.asyncio
async def test_list_traces_keeps_other_named_traces(client, monkeypatch):
    """非 Hermes turn 的 trace（chat_completion / run / 自定义 name）都保留。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([
            _trace(trace_id="gw-1", name="chat_completion"),
            _trace(trace_id="gw-2", name="run"),
            _trace(trace_id="custom-1", name="dify_workflow"),
        ])),
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),
    )

    resp = await client.get("/api/manager/observability/traces")
    assert resp.status_code == 200
    body = resp.json()
    ids = [it["id"] for it in body["items"]]
    assert ids == ["gw-1", "gw-2", "custom-1"]


@pytest.mark.asyncio
async def test_list_traces_hermes_turn_only_returns_empty(client, monkeypatch):
    """全是 Hermes turn 的情况返回空列表（不报错）。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([
            _trace(trace_id="hermes-1", name="Hermes turn"),
            _trace(trace_id="hermes-2", name="Hermes turn"),
        ])),
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),
    )

    resp = await client.get("/api/manager/observability/traces")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    # total 是 meta.totalItems（包含 Hermes turn 计数），分页器照常显示
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_list_traces_langfuse_not_configured(client, monkeypatch):
    """Langfuse 未配置 → 空列表 + langfuse_configured=false。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)
    resp = await client.get("/api/manager/observability/traces")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["langfuse_configured"] is False
