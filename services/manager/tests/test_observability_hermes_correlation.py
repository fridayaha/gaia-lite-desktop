"""Hermes Langfuse 关联查询 endpoint 单元测试。

验证 ``GET /api/manager/observability/traces/{trace_id}/hermes-correlation``:

新逻辑（2026-07-17 重构）：
- Hermes merged trace 的 trace.input 只是首次创建时的 input，不代表后续 merge
  进来的请求——所以不能再用 trace.input 哈希做关联。
- 改为在 hermes trace 的 observations 里找 ``name="Hermes turn" type=CHAIN``
  的子 turn observation，用子 turn.input 哈希匹配 Gateway hash；多条匹配
  时按 startTime 跟 gateway_request_time 最近选 + ±10min 时间窗（防误关联）。
- 命中后只返回子 turn + 它下面的子树 observations（按 parentObservationId 链
  递归收集），不返回 merged trace 里其他子 turn 的 observations。
- 单 turn trace 场景（trace 没有子 turn observation）fallback 用 trace.input
  匹配 + trace.timestamp 时间窗。

reason 取值：
- langfuse_not_configured / gateway_trace_not_found /
  no_correlation_keys_in_gateway_trace / list_traces_failed /
  no_matching_hermes_trace / sub_turn_hash_matched /
  trace_input_hash_matched（单 turn 场景）/ direct_llm_call（/v1/chat/completions
  直接 LLM 代理，Hermes 不进 agent loop，无内部 trace）
"""

from unittest.mock import AsyncMock

import pytest


def _gw_trace(
    *,
    trace_id: str = "gw-trace-1",
    session_id: str = "sess-1",
    last_user_message_hash: str = "abc123def456abc1",
    gateway_request_time: float = 1752700000.0,
) -> dict:
    """构造一个 Gateway trace 对象。"""
    return {
        "id": trace_id,
        "sessionId": session_id,
        "metadata": {
            "last_user_message_hash": last_user_message_hash,
            "gateway_request_time": gateway_request_time,
            "agent_id": "agent-1",
        },
    }


def _hermes_trace(
    *,
    trace_id: str = "hermes-trace-1",
    session_id: str = "sess-1",
    last_user_message: str = "hello",
    timestamp: str = "2026-07-17T10:00:00Z",
) -> dict:
    """构造一个 Hermes 内层 trace 对象，input 是单条 message dict 形式
    （对齐 Hermes langfuse 插件实际写的 trace.input）。"""
    return {
        "id": trace_id,
        "sessionId": session_id,
        "name": "Hermes turn",
        "timestamp": timestamp,
        "input": {"role": "user", "content": last_user_message},
    }


def _sub_turn(
    *,
    obs_id: str,
    user_message: str,
    start_time: str,
    parent_id: str | None = "parent-1",
    trace_id: str = "hermes-trace-1",
) -> dict:
    """构造一个子 turn observation（name=Hermes turn type=CHAIN）。

    这是 merged trace 下每个请求对应的子 turn。
    """
    return {
        "id": obs_id,
        "traceId": trace_id,
        "type": "CHAIN",
        "name": "Hermes turn",
        "parentObservationId": parent_id,
        "startTime": start_time,
        "endTime": start_time,
        "input": {"role": "user", "content": user_message},
    }


def _llm_call(
    *,
    obs_id: str,
    parent_id: str,
    start_time: str,
    name: str = "LLM call 1",
) -> dict:
    """构造一个 LLM call observation，挂在某个 parent 下。"""
    return {
        "id": obs_id,
        "type": "GENERATION",
        "name": name,
        "parentObservationId": parent_id,
        "startTime": start_time,
        "endTime": start_time,
        "input": [{"role": "assistant", "content": "..."}],
    }


def _tool_call(
    *,
    obs_id: str,
    parent_id: str,
    start_time: str,
    name: str = "Tool: read_file",
) -> dict:
    """构造一个 tool call observation。"""
    return {
        "id": obs_id,
        "type": "TOOL",
        "name": name,
        "parentObservationId": parent_id,
        "startTime": start_time,
        "endTime": start_time,
    }


def _list_traces_response(traces: list[dict]) -> dict:
    """构造 list_traces 的 v3 响应格式 {data: [...], meta: ...}。"""
    return {"data": traces, "meta": {"totalItems": len(traces)}}


@pytest.mark.asyncio
async def test_langfuse_not_configured_returns_disabled(client, monkeypatch):
    """Langfuse 未配置 → langfuse_configured=false。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)
    resp = await client.get("/api/manager/observability/traces/gw-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["langfuse_configured"] is False
    assert body["hermes_trace"] is None
    assert body["reason"] == "langfuse_not_configured"


@pytest.mark.asyncio
async def test_gateway_trace_not_found_returns_reason(client, monkeypatch):
    """get_trace 返回 None → reason=gateway_trace_not_found。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(return_value=None)
    )
    resp = await client.get("/api/manager/observability/traces/gw-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "gateway_trace_not_found"
    assert body["hermes_trace"] is None


@pytest.mark.asyncio
async def test_gateway_trace_missing_session_id_returns_reason(client, monkeypatch):
    """Gateway trace 无 sessionId → reason=no_correlation_keys_in_gateway_trace。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "get_trace",
        AsyncMock(return_value={
            "id": "gw-1",
            "sessionId": None,
            "metadata": {"last_user_message_hash": "abc", "gateway_request_time": 1752700000.0},
        }),
    )
    resp = await client.get("/api/manager/observability/traces/gw-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "no_correlation_keys_in_gateway_trace"


@pytest.mark.asyncio
async def test_gateway_trace_missing_hash_returns_reason(client, monkeypatch):
    """Gateway trace 无 last_user_message_hash → no_correlation_keys_in_gateway_trace。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "get_trace",
        AsyncMock(return_value={
            "id": "gw-1",
            "sessionId": "sess-1",
            "metadata": {"gateway_request_time": 1752700000.0},
        }),
    )
    resp = await client.get("/api/manager/observability/traces/gw-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "no_correlation_keys_in_gateway_trace"


@pytest.mark.asyncio
async def test_gateway_trace_path_chat_completions_returns_direct_llm_call(client, monkeypatch):
    """Gateway trace metadata.path == "v1/chat/completions" → reason=direct_llm_call。

    /v1/chat/completions 走直接 LLM 代理路径，Hermes 不进 agent loop，
    不会写 "Hermes turn" 内部 trace。早返回，不查 list_traces / list_observations。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    # Gateway trace 带 path=v1/chat/completions（与真实 4f613774 trace 结构一致）
    gw_trace = {
        "id": "gw-1",
        "sessionId": "api_1784258806_67d793b1",
        "metadata": {
            "engine_type": "HERMES",
            "path": "v1/chat/completions",
            "method": "POST",
            "channel_type": "web",
            "last_user_message_hash": "7c9691192f1b7340",
            "gateway_request_time": 1784288740.7919166,
        },
    }
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(return_value=gw_trace)
    )

    # list_traces / list_observations 不应被调用（早返回）
    list_traces_mock = AsyncMock(return_value={"data": [], "meta": {"totalItems": 0}})
    list_observations_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(observability.langfuse_client, "list_traces", list_traces_mock)
    monkeypatch.setattr(
        observability.langfuse_client, "list_observations", list_observations_mock
    )

    resp = await client.get("/api/manager/observability/traces/gw-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "direct_llm_call"
    assert body["hermes_trace"] is None
    assert body["observations"] == []
    assert body["langfuse_configured"] is True
    # 早返回，不应调 list_traces / list_observations
    list_traces_mock.assert_not_awaited()
    list_observations_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_trace_path_runs_does_not_short_circuit(client, monkeypatch):
    """path=v1/runs 时不走 direct_llm_call 早返回，继续查 Hermes turn trace。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    gw_trace = _gw_trace()
    # _gw_trace 默认 metadata 没有 path，模拟 v1/runs 路径
    gw_trace["metadata"]["path"] = "v1/runs"
    gw_trace["metadata"]["engine_type"] = "HERMES"
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(return_value=gw_trace)
    )

    # list_traces 被调用，返回空（最终落到 no_matching_hermes_trace）
    list_traces_mock = AsyncMock(return_value={"data": [], "meta": {"totalItems": 0}})
    monkeypatch.setattr(observability.langfuse_client, "list_traces", list_traces_mock)
    monkeypatch.setattr(
        observability.langfuse_client, "list_observations", AsyncMock(return_value=[])
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "no_matching_hermes_trace"
    list_traces_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_traces_returns_none_returns_reason(client, monkeypatch):
    """list_traces 调用失败返回 None → reason=list_traces_failed。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "get_trace",
        AsyncMock(return_value=_gw_trace()),
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=None),
    )
    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "list_traces_failed"


@pytest.mark.asyncio
async def test_list_traces_filtered_by_name_hermes_turn(client, monkeypatch):
    """list_traces 必须传 name="Hermes turn" 过滤掉 Gateway 自己写的 run trace。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "hello"}]})

    gw_trace = _gw_trace(last_user_message_hash=gw_hash)
    hermes_trace = _hermes_trace(last_user_message="hello")

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )

    # 捕获 list_traces 实际传参
    list_traces_calls = []
    async def _mock_list_traces(**kwargs):
        list_traces_calls.append(kwargs)
        return _list_traces_response([hermes_trace])
    monkeypatch.setattr(
        observability.langfuse_client, "list_traces", AsyncMock(side_effect=_mock_list_traces)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),
    )

    await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    assert len(list_traces_calls) == 1
    assert list_traces_calls[0].get("name") == "Hermes turn"
    assert list_traces_calls[0].get("session_id") == "sess-1"


@pytest.mark.asyncio
async def test_no_matching_hermes_trace_returns_reason(client, monkeypatch):
    """候选 hermes trace 的所有子 turn input 都不匹配 → no_matching_hermes_trace。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "hello"}]})

    gw_trace = _gw_trace(last_user_message_hash=gw_hash)
    hermes_trace = _hermes_trace(last_user_message="different")

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        if tid == "hermes-trace-1":
            return hermes_trace
        # 确定性 trace_id 直取未命中（本测例聚焦 list 候选路径）
        return None
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # trace 的子 turn input 不匹配
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[
            _sub_turn(obs_id="sub-1", user_message="different", start_time="2026-07-17T10:00:00Z"),
        ]),
    )
    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "no_matching_hermes_trace"
    assert body["hermes_trace"] is None
    assert body["candidate_count"] == 1


@pytest.mark.asyncio
async def test_single_turn_trace_hash_matched(client, monkeypatch):
    """单 turn trace（无子 turn observation）+ trace.input 哈希匹配 + timestamp 时间窗内 → 命中。

    场景：task_id != session_id（非 Portal 请求），Hermes 每次请求独立写 trace，
    没有 merged trace，trace.input 就是当前请求内容。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "hello"}]})

    # Gateway 在 10:00:00 收到请求
    gw_trace = _gw_trace(last_user_message_hash=gw_hash, gateway_request_time=1752700000.0)
    # Hermes trace timestamp 在 10:00:30（30s 后），在 ±10min 时间窗内
    hermes_trace = _hermes_trace(
        last_user_message="hello",
        timestamp="2026-07-17T10:00:30Z",
    )
    # timestamp 转 unix 秒应 ≈ 1752700030
    # 实际 gw_trace.gateway_request_time=1752700000.0 是占位值，下面用真实 ISO 时间窗测试

    # 重设 gw_trace gateway_request_time 跟 hermes_trace.timestamp 对齐
    # 2026-07-17T10:00:30Z → unix ≈ 1784251230
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 10, 0, 30, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(
        last_user_message_hash=gw_hash,
        gateway_request_time=gw_req_time,
    )

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # 单 turn trace：observations 里没有 name=Hermes turn type=CHAIN 子 turn
    fake_observations = [
        {"id": "obs-1", "type": "GENERATION", "name": "LLM call 1",
         "startTime": "2026-07-17T10:00:30Z"},
    ]
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=fake_observations),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "trace_input_hash_matched"
    assert body["hermes_trace"]["id"] == "hermes-trace-1"
    assert body["observations"] == fake_observations
    assert body["matched_sub_turn_id"] is None


@pytest.mark.asyncio
async def test_single_turn_trace_timestamp_outside_window_returns_no_match(client, monkeypatch):
    """单 turn trace 哈希匹配但 trace.timestamp 超出 ±10min 时间窗 → no_matching_hermes_trace。

    防误关联：同 session 同文本的不同请求，trace.timestamp 跟 gw_req_time 相差太大
    说明不是同一次请求。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    # Gateway 11:45:40 收到请求
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 11, 45, 40, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(
        last_user_message_hash=gw_hash,
        gateway_request_time=gw_req_time,
    )
    # Hermes trace timestamp=03:43:58（8 小时前），明显不是同一次请求
    hermes_trace = _hermes_trace(
        last_user_message="继续",
        timestamp="2026-07-17T03:43:58Z",
    )

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),  # 无子 turn → 走单 turn 路径
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "no_matching_hermes_trace"
    assert body["hermes_trace"] is None


@pytest.mark.asyncio
async def test_merged_trace_subturn_hash_matched_returns_only_subtree(client, monkeypatch):
    """merged trace 场景：子 turn.input 哈希匹配 + startTime 时间窗内 → 返回子 turn + 子树 observations。

    核心断言：只返回匹配子 turn 自己 + 它下面的 LLM call / tool call，
    不返回 merged trace 里其他子 turn 的 observations。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    # Gateway 在 10:27:44 收到请求
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 10, 27, 44, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(
        last_user_message_hash=gw_hash,
        gateway_request_time=gw_req_time,
    )
    # Hermes merged trace（trace.input 是首次创建时的，跟 Gateway 哈希匹配是巧合）
    hermes_trace = _hermes_trace(
        last_user_message="继续",  # 跟 Gateway 哈希一样，但 trace.input 不能用于 merged trace
        timestamp="2026-07-17T03:43:58Z",  # 首次创建时间，远早于 Gateway
    )

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # merged trace 的 48 条 observations 里有两个子 turn（都 input=继续）
    # 子 turn A startTime=10:27:44（跟 Gateway 时间一致）→ 命中
    # 子 turn B startTime=10:06:11（早 21 分钟）→ 时间窗内
    # 还有其他子 turn input 不匹配
    observations = [
        _sub_turn(obs_id="sub-a", user_message="继续", start_time="2026-07-17T10:27:44Z", parent_id="p-a"),
        _llm_call(obs_id="llm-a-1", parent_id="sub-a", start_time="2026-07-17T10:27:44Z"),
        _tool_call(obs_id="tool-a-1", parent_id="sub-a", start_time="2026-07-17T10:27:46Z", name="Tool: patch"),
        _llm_call(obs_id="llm-a-2", parent_id="sub-a", start_time="2026-07-17T10:28:32Z", name="LLM call 2"),
        _sub_turn(obs_id="sub-b", user_message="继续", start_time="2026-07-17T10:06:11Z", parent_id="p-b"),
        _llm_call(obs_id="llm-b-1", parent_id="sub-b", start_time="2026-07-17T10:06:11Z"),
        _sub_turn(obs_id="sub-c", user_message="hello", start_time="2026-07-17T03:43:58Z", parent_id="p-c"),
        _llm_call(obs_id="llm-c-1", parent_id="sub-c", start_time="2026-07-17T03:43:58Z"),
    ]
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=observations),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "sub_turn_hash_matched"
    assert body["hermes_trace"]["id"] == "hermes-trace-1"
    assert body["matched_sub_turn_id"] == "sub-a"
    # 只返回 sub-a + 它下面的子树（llm-a-1, tool-a-1, llm-a-2）
    # 不返回 sub-b（虽然 input 哈希也匹配，但 startTime 距 Gateway 21 分钟——仍在 10min 窗外，被剔除）
    # 不返回 sub-c 的子树（input 不匹配）
    returned_ids = [o["id"] for o in body["observations"]]
    assert returned_ids == ["sub-a", "llm-a-1", "tool-a-1", "llm-a-2"]
    assert "sub-b" not in returned_ids
    assert "sub-c" not in returned_ids
    assert "llm-b-1" not in returned_ids
    assert "llm-c-1" not in returned_ids


@pytest.mark.asyncio
async def test_merged_trace_subturn_outside_time_window_returns_no_match(client, monkeypatch):
    """merged trace 子 turn 哈希匹配但 startTime 超出 ±10min 时间窗 → no_match。

    防误关联：4f613774（11:45:40 的"继续"）实际不在 91ec84f0 trace 里，
    但 trace.input="继续"哈希跟 Gateway 一致——旧逻辑会误关联。
    新逻辑要求子 turn.startTime 跟 gw_req_time 在 ±10min 内，避免误关联。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    # Gateway 11:45:40
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 11, 45, 40, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(
        last_user_message_hash=gw_hash,
        gateway_request_time=gw_req_time,
    )
    # Hermes trace timestamp=03:43:58，trace.input=继续（hash 巧合匹配）
    hermes_trace = _hermes_trace(
        last_user_message="继续",
        timestamp="2026-07-17T03:43:58Z",
    )

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # 子 turn startTime=10:27:44（距 Gateway 11:45:40 差 1h17min，超 10min 窗）
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[
            _sub_turn(obs_id="sub-a", user_message="继续", start_time="2026-07-17T10:27:44Z"),
            _llm_call(obs_id="llm-a-1", parent_id="sub-a", start_time="2026-07-17T10:27:44Z"),
        ]),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "no_matching_hermes_trace"
    assert body["hermes_trace"] is None


@pytest.mark.asyncio
async def test_merged_trace_picks_closest_subturn_by_starttime(client, monkeypatch):
    """多条子 turn 哈希匹配且都在时间窗内 → 按 startTime 跟 gw_req_time 最近选。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    # Gateway 10:27:50
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 10, 27, 50, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(
        last_user_message_hash=gw_hash,
        gateway_request_time=gw_req_time,
    )
    hermes_trace = _hermes_trace(last_user_message="继续", timestamp="2026-07-17T03:43:58Z")

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # 两个子 turn 都 input=继续 + 在 ±10min 窗内
    # sub-close: 10:27:44（距 10:27:50 差 6s）→ 命中
    # sub-far: 10:25:00（距 10:27:50 差 2min50s）→ 也匹配但更远
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[
            _sub_turn(obs_id="sub-far", user_message="继续", start_time="2026-07-17T10:25:00Z", parent_id="p-far"),
            _llm_call(obs_id="llm-far-1", parent_id="sub-far", start_time="2026-07-17T10:25:00Z"),
            _sub_turn(obs_id="sub-close", user_message="继续", start_time="2026-07-17T10:27:44Z", parent_id="p-close"),
            _llm_call(obs_id="llm-close-1", parent_id="sub-close", start_time="2026-07-17T10:27:44Z"),
        ]),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "sub_turn_hash_matched"
    assert body["matched_sub_turn_id"] == "sub-close"
    returned_ids = [o["id"] for o in body["observations"]]
    assert returned_ids == ["sub-close", "llm-close-1"]


@pytest.mark.asyncio
async def test_subtree_collection_includes_recursive_descendants(client, monkeypatch):
    """子 turn + 多层嵌套 observations（LLM call 下挂 tool call 下挂 LLM call）全返回。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 17, 10, 27, 44, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(last_user_message_hash=gw_hash, gateway_request_time=gw_req_time)
    hermes_trace = _hermes_trace(last_user_message="继续", timestamp="2026-07-17T03:43:58Z")

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # 子 turn → LLM call 1 → Tool: read_file → LLM call 2
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[
            _sub_turn(obs_id="sub-a", user_message="继续", start_time="2026-07-17T10:27:44Z", parent_id="p-a"),
            _llm_call(obs_id="llm-1", parent_id="sub-a", start_time="2026-07-17T10:27:44Z"),
            _tool_call(obs_id="tool-1", parent_id="llm-1", start_time="2026-07-17T10:27:45Z", name="Tool: read_file"),
            _llm_call(obs_id="llm-2", parent_id="tool-1", start_time="2026-07-17T10:27:46Z", name="LLM call 2"),
        ]),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["matched_sub_turn_id"] == "sub-a"
    returned_ids = [o["id"] for o in body["observations"]]
    assert returned_ids == ["sub-a", "llm-1", "tool-1", "llm-2"]


@pytest.mark.asyncio
async def test_gateway_trace_without_request_time_falls_back_to_latest_subturn(client, monkeypatch):
    """无 gateway_request_time → 退化为按 startTime DESC 取最新匹配子 turn（无时间窗）。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "继续"}]})

    gw_trace = {
        "id": "gw-trace-1",
        "sessionId": "sess-1",
        "metadata": {"last_user_message_hash": gw_hash},  # 无 gateway_request_time
    }
    hermes_trace = _hermes_trace(last_user_message="继续", timestamp="2026-07-17T03:43:58Z")

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        return hermes_trace
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    # 两个匹配子 turn（startTime 不同）→ 取 startTime 更新的
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[
            _sub_turn(obs_id="sub-old", user_message="继续", start_time="2026-07-17T10:06:11Z", parent_id="p-old"),
            _llm_call(obs_id="llm-old-1", parent_id="sub-old", start_time="2026-07-17T10:06:11Z"),
            _sub_turn(obs_id="sub-new", user_message="继续", start_time="2026-07-17T10:27:44Z", parent_id="p-new"),
            _llm_call(obs_id="llm-new-1", parent_id="sub-new", start_time="2026-07-17T10:27:44Z"),
        ]),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "sub_turn_hash_matched"
    assert body["matched_sub_turn_id"] == "sub-new"
    returned_ids = [o["id"] for o in body["observations"]]
    assert returned_ids == ["sub-new", "llm-new-1"]


# ═══════════════════════════════════════════════════════════
# 确定性 trace_id 直取（sessionId 错位污染场景，2026-07-22 定位）
# ═══════════════════════════════════════════════════════════

class TestHermesSessionTraceId:
    """hermes_session_trace_id 复算 Hermes 插件 create_trace_id(seed) 结果。

    三个断言向量都是 ECS 真实 trace：插件实际生成的 trace id 与本地复算值
    逐一核对一致（2026-07-22 抓包验证）。
    """

    def test_real_world_vectors(self):
        from pkg.common.langfuse_correlation import hermes_session_trace_id
        vectors = {
            "api_1784711924_4b3d31c4": "9276007218d3a19624b32544fa109309",
            "api_1784727096_1c91c900": "3ce1c42ae957037db4f2b04a7013c488",
            "api_1784735000_probe99": "ac1d956de971d45a2f05420ea1b18341",
        }
        for session_id, expected in vectors.items():
            assert hermes_session_trace_id(session_id) == expected

    def test_format(self):
        from pkg.common.langfuse_correlation import hermes_session_trace_id
        tid = hermes_session_trace_id("sess-1")
        assert len(tid) == 32
        int(tid, 16)  # 纯 hex
        # 确定性
        assert hermes_session_trace_id("sess-1") == tid
        assert hermes_session_trace_id("sess-2") != tid


@pytest.mark.asyncio
async def test_deterministic_id_hit_when_session_id_polluted(client, monkeypatch):
    """trace 行 sessionId 错位污染：list_traces(sessionId=当前) 只捞到污染 decoy，
    确定性 trace_id 直取命中正确 trace → sub_turn_hash_matched。

    复刻 2026-07-22 真实事故：长寿命 profile 进程把上一 run 的 session 写进
    下一 run 的 trace 行 sessionId，按 sessionId 过滤拿不到正确 trace；
    同时反向污染会产生"背着当前 sessionId 的别人的 trace"（decoy），
    其内容哈希不匹配，不得误关联。
    """
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message, hermes_session_trace_id
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "hello"}]})
    import datetime as _dt
    gw_req_time = _dt.datetime(2026, 7, 22, 15, 12, 30, tzinfo=_dt.timezone.utc).timestamp()
    gw_trace = _gw_trace(last_user_message_hash=gw_hash, gateway_request_time=gw_req_time)

    det_id = hermes_session_trace_id("sess-1")
    # 正确 trace：id = 确定性复算值，trace 行 sessionId 是上一 run 的（污染）
    right_trace = _hermes_trace(
        trace_id=det_id, session_id="prev-run-session",
        last_user_message="hello", timestamp="2026-07-22T15:12:32Z",
    )
    # decoy：反向污染产物，背着当前 sessionId，但内容是别人的消息
    decoy_trace = _hermes_trace(
        trace_id="decoy-trace", session_id="sess-1",
        last_user_message="someone-else", timestamp="2026-07-22T15:37:52Z",
    )

    async def _mock_get_trace(tid):
        if tid == "gw-trace-1":
            return gw_trace
        if tid == det_id:
            return right_trace
        if tid == "decoy-trace":
            return decoy_trace
        return None
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    # list 只捞到 decoy（正确 trace 的 sessionId 是上一 run 的，过滤不到）
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([decoy_trace])),
    )

    async def _mock_list_observations(trace_id=None, **kwargs):
        if trace_id == det_id:
            return [
                _sub_turn(obs_id="sub-hit", user_message="hello",
                          start_time="2026-07-22T15:12:32Z", parent_id="p-1", trace_id=det_id),
                _llm_call(obs_id="llm-1", parent_id="sub-hit", start_time="2026-07-22T15:12:33Z"),
            ]
        return [
            _sub_turn(obs_id="sub-decoy", user_message="someone-else",
                      start_time="2026-07-22T15:37:52Z", parent_id="p-2", trace_id="decoy-trace"),
        ]
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(side_effect=_mock_list_observations),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["reason"] == "sub_turn_hash_matched"
    assert body["hermes_trace"]["id"] == det_id
    assert body["matched_sub_turn_id"] == "sub-hit"
    returned_ids = [o["id"] for o in body["observations"]]
    assert returned_ids == ["sub-hit", "llm-1"]
    # decoy 参与候选（candidate_count=2）但不得误关联
    assert body["candidate_count"] == 2


@pytest.mark.asyncio
async def test_deterministic_id_deduped_when_list_already_contains(client, monkeypatch):
    """list_traces 结果已含确定性 id（未污染场景）→ 不重复 fetch，candidate_count=1。"""
    from app.api import observability
    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    from pkg.common.langfuse_correlation import hash_last_user_message, hermes_session_trace_id
    gw_hash = hash_last_user_message({"messages": [{"role": "user", "content": "hello"}]})
    gw_trace = _gw_trace(last_user_message_hash=gw_hash)

    det_id = hermes_session_trace_id("sess-1")
    hermes_trace = _hermes_trace(trace_id=det_id, last_user_message="hello")

    get_trace_calls = []
    async def _mock_get_trace(tid):
        get_trace_calls.append(tid)
        if tid == "gw-trace-1":
            return gw_trace
        if tid == det_id:
            return hermes_trace
        return None
    monkeypatch.setattr(
        observability.langfuse_client, "get_trace", AsyncMock(side_effect=_mock_get_trace)
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=_list_traces_response([hermes_trace])),
    )
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_observations",
        AsyncMock(return_value=[]),
    )

    resp = await client.get("/api/manager/observability/traces/gw-trace-1/hermes-correlation")
    body = resp.json()
    assert body["candidate_count"] == 1
    # get_trace 只调两次：gateway trace + 候选 det_id 各一次（无重复 det fetch）
    assert get_trace_calls.count(det_id) == 1
