"""Dify 外接模式用量反查 collector 单元测试。

覆盖：
- build_langfuse_config: EngineConfig → LangfuseConfig（含 Fernet 解密）
- _iso_to_date_str: ISO 8601 timestamp → 'YYYY-MM-DD'（UTC）
- _fetch_all_traces: 拉时间窗口内全部 trace（分页，不带 metadata 过滤）
- _group_traces_by_app_id: 客户端按 trace.metadata.app_id 分组
- _fetch_app_costs: 调 Dify Console API 拿 per-day per-app cost（mock DifyConsoleClient）
- _collect_trace_details: per-trace 维度，token 从 trace.input 反查，cost 按 (app_id, date) 平摊
- collect_dify_usage: 未配置 Langfuse 抛 ValueError；空 agent_meta_map 直接返回 []

并覆盖底层依赖：
- langfuse_client._resolve: per-EngineConfig config 参数
- langfuse_client.list_traces: metadata[<k>]=<v> 过滤
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 让 tests/ 能 import app.* / pkg.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.crypto import encrypt_credential  # noqa: E402
from app.services.langfuse_client import LangfuseConfig  # noqa: E402


# ── 测试夹具：构造 EngineConfig-like 对象 ─────────────────────────


def _make_engine_config(
    *,
    base_url: str | None = "https://dify.example.com",
    admin_email: str | None = "admin@example.com",
    admin_password: str | None = "secret-password",
    langfuse_host: str | None = "https://lf.example.com",
    langfuse_public_key: str | None = "pk-lf-xxx",
    langfuse_secret_key: str | None = "sk-lf-yyy",
    langfuse_secret_key_encrypted: str | None = None,
):
    """构造一个 mock EngineConfig（用 SimpleNamespace 模拟列属性）。"""
    from types import SimpleNamespace

    if langfuse_secret_key_encrypted is None and langfuse_secret_key:
        langfuse_secret_key_encrypted = encrypt_credential(langfuse_secret_key)
    admin_password_encrypted = None
    if admin_password:
        admin_password_encrypted = encrypt_credential(admin_password)
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        base_url=base_url,
        admin_email=admin_email,
        admin_password_encrypted=admin_password_encrypted,
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key_encrypted=langfuse_secret_key_encrypted,
    )


# ── build_langfuse_config ─────────────────────────────────────────


def test_build_langfuse_config_returns_none_when_host_missing():
    from app.services.dify_usage_collector import build_langfuse_config

    cfg = _make_engine_config(langfuse_host=None)
    assert build_langfuse_config(cfg) is None


def test_build_langfuse_config_returns_none_when_public_key_missing():
    from app.services.dify_usage_collector import build_langfuse_config

    cfg = _make_engine_config(langfuse_public_key=None)
    assert build_langfuse_config(cfg) is None


def test_build_langfuse_config_returns_none_when_secret_missing():
    from app.services.dify_usage_collector import build_langfuse_config

    cfg = _make_engine_config(langfuse_secret_key=None, langfuse_secret_key_encrypted=None)
    assert build_langfuse_config(cfg) is None


def test_build_langfuse_config_returns_config_when_all_fields_present():
    from app.services.dify_usage_collector import build_langfuse_config

    cfg = _make_engine_config(
        langfuse_host="https://lf.example.com/",
        langfuse_public_key="pk-lf-xxx",
        langfuse_secret_key="sk-lf-yyy",
    )
    result = build_langfuse_config(cfg)
    assert isinstance(result, LangfuseConfig)
    assert result.base_url == "https://lf.example.com/"
    assert result.public_key == "pk-lf-xxx"
    assert result.secret_key == "sk-lf-yyy"


def test_build_langfuse_config_returns_none_when_decrypt_fails():
    """Fernet token 无效时返回 None（不抛异常给调用方）。"""
    from app.services.dify_usage_collector import build_langfuse_config

    cfg = _make_engine_config(
        langfuse_secret_key=None,
        langfuse_secret_key_encrypted="not-a-valid-fernet-token",
    )
    assert build_langfuse_config(cfg) is None


# ── _iso_to_date_str ──────────────────────────────────────────────


def test_iso_to_date_str_parses_utc_iso():
    from app.services.dify_usage_collector import _iso_to_date_str

    assert _iso_to_date_str("2026-07-04T13:02:44Z") == "2026-07-04"
    assert _iso_to_date_str("2026-07-04T13:02:44+00:00") == "2026-07-04"


def test_iso_to_date_str_returns_none_for_invalid_input():
    from app.services.dify_usage_collector import _iso_to_date_str

    assert _iso_to_date_str(None) is None
    assert _iso_to_date_str("") is None
    assert _iso_to_date_str("not-a-timestamp") is None


# ── _fetch_all_traces ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_all_traces_single_page(monkeypatch):
    """单页结果：返回所有 trace，不重复拉取。"""
    from app.services import dify_usage_collector

    calls: list[dict] = []

    async def _mock_list_traces(**kwargs):
        calls.append(kwargs)
        return {
            "data": [{"id": "t1"}, {"id": "t2"}],
            "meta": {"page": 1, "totalPages": 1},
        }

    monkeypatch.setattr(dify_usage_collector, "list_traces", _mock_list_traces)
    result = await dify_usage_collector._fetch_all_traces(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert len(result) == 2
    assert len(calls) == 1
    assert calls[0]["from_ts"] == "2026-06-01T00:00:00Z"
    assert calls[0]["to_ts"] == "2026-07-03T00:00:00Z"


@pytest.mark.asyncio
async def test_fetch_all_traces_paginates_until_last_page(monkeypatch):
    """多页结果：循环拉取直到 page >= totalPages。"""
    from app.services import dify_usage_collector

    calls: list[int] = []

    async def _mock_list_traces(**kwargs):
        calls.append(kwargs["offset"])
        page = kwargs["offset"] // 100 + 1
        return {
            "data": [{"id": f"t{page}"}],
            "meta": {"page": page, "totalPages": 3},
        }

    monkeypatch.setattr(dify_usage_collector, "list_traces", _mock_list_traces)
    result = await dify_usage_collector._fetch_all_traces(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert len(result) == 3
    assert calls == [0, 100, 200]


@pytest.mark.asyncio
async def test_fetch_all_traces_caps_at_max_traces(monkeypatch):
    """超过 _MAX_TRACES 上限立即停止（防爆拉）。"""
    from app.services import dify_usage_collector

    async def _mock_list_traces(**kwargs):
        offset = kwargs["offset"]
        page = offset // 100 + 1
        return {
            "data": [{"id": f"t{offset}-{i}"} for i in range(100)],
            "meta": {"page": page, "totalPages": 999},
        }

    monkeypatch.setattr(dify_usage_collector, "list_traces", _mock_list_traces)
    monkeypatch.setattr(dify_usage_collector, "_MAX_TRACES", 250)
    result = await dify_usage_collector._fetch_all_traces(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert len(result) == 300


@pytest.mark.asyncio
async def test_fetch_all_traces_empty_response_stops(monkeypatch):
    """Langfuse 返回 None 或空 data 时停止。"""
    from app.services import dify_usage_collector

    async def _mock_list_traces(**kwargs):
        return None

    monkeypatch.setattr(dify_usage_collector, "list_traces", _mock_list_traces)
    result = await dify_usage_collector._fetch_all_traces(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert result == []


# ── _fetch_all_observations ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_all_observations_single_page(monkeypatch):
    """单页结果（< 100 条）：拉一次返回，不分页。"""
    from app.services import dify_usage_collector

    calls: list[dict] = []

    async def _mock_list_observations(**kwargs):
        calls.append(kwargs)
        return [{"id": "o1"}, {"id": "o2"}]

    monkeypatch.setattr(dify_usage_collector, "list_observations", _mock_list_observations)
    result = await dify_usage_collector._fetch_all_observations(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert len(result) == 2
    assert len(calls) == 1
    assert calls[0]["type"] == "GENERATION"
    assert calls[0]["from_ts"] == "2026-06-01T00:00:00Z"
    assert calls[0]["to_ts"] == "2026-07-03T00:00:00Z"


@pytest.mark.asyncio
async def test_fetch_all_observations_paginates_until_less_than_full_page(monkeypatch):
    """多页结果：循环拉取直到 batch < 100（list_observations 无 totalPages，靠 batch 大小判断）。"""
    from app.services import dify_usage_collector

    calls: list[int] = []

    async def _mock_list_observations(**kwargs):
        calls.append(kwargs["offset"])
        offset = kwargs["offset"]
        if offset == 0:
            return [{"id": f"o{i}"} for i in range(100)]  # 满页，继续
        return [{"id": f"o{offset}-1"}]  # 不满页，停止

    monkeypatch.setattr(dify_usage_collector, "list_observations", _mock_list_observations)
    result = await dify_usage_collector._fetch_all_observations(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert len(result) == 101
    assert calls == [0, 100]


@pytest.mark.asyncio
async def test_fetch_all_observations_empty_response_stops(monkeypatch):
    """Langfuse 返回 None 或空 list 时停止。"""
    from app.services import dify_usage_collector

    async def _mock_list_observations(**kwargs):
        return None

    monkeypatch.setattr(dify_usage_collector, "list_observations", _mock_list_observations)
    result = await dify_usage_collector._fetch_all_observations(
        "2026-06-01T00:00:00Z", "2026-07-03T00:00:00Z",
        LangfuseConfig(base_url="https://lf.example.com", public_key="pk", secret_key="sk"),
    )
    assert result == []


# ── _group_traces_by_app_id ───────────────────────────────────────


def test_group_traces_by_app_id_groups_correctly():
    from app.services.dify_usage_collector import _group_traces_by_app_id

    traces = [
        {"id": "t1", "metadata": {"app_id": "app-a"}},
        {"id": "t2", "metadata": {"app_id": "app-a"}},
        {"id": "t3", "metadata": {"app_id": "app-b"}},
    ]
    grouped = _group_traces_by_app_id(traces)
    assert set(grouped.keys()) == {"app-a", "app-b"}
    assert len(grouped["app-a"]) == 2
    assert len(grouped["app-b"]) == 1


def test_group_traces_by_app_id_skips_missing_app_id():
    from app.services.dify_usage_collector import _group_traces_by_app_id

    traces = [
        {"id": "t1", "metadata": {"app_id": "app-a"}},
        {"id": "t2", "metadata": {"engine_type": "HERMES"}},
        {"id": "t3", "metadata": None},
        {"id": "t4"},
    ]
    grouped = _group_traces_by_app_id(traces)
    assert list(grouped.keys()) == ["app-a"]


def test_group_traces_by_app_id_handles_non_dict_metadata():
    from app.services.dify_usage_collector import _group_traces_by_app_id

    traces = [
        {"id": "t1", "metadata": "not-a-dict"},
        {"id": "t2", "metadata": ["list"]},
        {"id": "t3", "metadata": {"app_id": "app-a"}},
    ]
    grouped = _group_traces_by_app_id(traces)
    assert list(grouped.keys()) == ["app-a"]


def test_group_traces_by_app_id_empty_input():
    from app.services.dify_usage_collector import _group_traces_by_app_id

    assert _group_traces_by_app_id([]) == {}


def test_group_traces_by_app_id_coerces_app_id_to_str():
    from app.services.dify_usage_collector import _group_traces_by_app_id

    traces = [{"id": "t1", "metadata": {"app_id": 12345}}]
    grouped = _group_traces_by_app_id(traces)
    assert list(grouped.keys()) == ["12345"]


# ── _fetch_app_costs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_app_costs_returns_empty_when_admin_not_configured(monkeypatch):
    """EngineConfig 未配 admin_email / admin_password → 返回空 dict（cost 降级 0）。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config(admin_email=None, admin_password=None)
    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}

    # DifyConsoleClient 不应被构造
    with patch("app.services.dify_usage_collector.DifyConsoleClient") as mock_ctor:
        result = await dify_usage_collector._fetch_app_costs(
            cfg, agent_meta_map, "2026-06-01T00:00:00Z", "2026-07-01T00:00:00Z"
        )
        assert mock_ctor.call_count == 0
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_app_costs_calls_get_app_token_costs_per_app(monkeypatch):
    """每个 app_id 调一次 get_app_token_costs，按 mode 选端点。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()

    # Mock DifyConsoleClient：list_apps 返回 mode 映射，get_app_token_costs 返回 per-day cost
    fake_client = MagicMock()
    fake_client.list_apps = AsyncMock(return_value=[
        {"id": "app-msg", "mode": "agent-chat"},
        {"id": "app-wf", "mode": "workflow"},
        {"id": "app-orphan", "mode": "chat"},  # 不在 agent_meta_map，应跳过
    ])
    fake_client.get_app_token_costs = AsyncMock(side_effect=[
        [{"date": "2026-07-04", "token_count": 400, "total_price": "0.0007920", "currency": "USD"}],
        [{"date": "2026-07-04", "token_count": "126"}],  # workflow 无 total_price
    ])
    fake_client.close = AsyncMock()

    with patch("app.services.dify_usage_collector.DifyConsoleClient", return_value=fake_client):
        result = await dify_usage_collector._fetch_app_costs(
            cfg,
            {"app-msg": {"agent_id": "a1"}, "app-wf": {"agent_id": "a2"}},
            "2026-06-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
        )

    # app-msg 有 cost，app-wf 无 total_price（被跳过）
    assert result == {"app-msg": {"2026-07-04": 0.000792}}
    # 调用顺序按 agent_meta_map.keys()，但 dict 顺序不保证，验证总次数
    assert fake_client.get_app_token_costs.call_count == 2
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_app_costs_skips_app_not_in_dify(monkeypatch):
    """agent_meta_map 含 app-x 但 Dify list_apps 没返回 → 跳过该 app。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()
    fake_client = MagicMock()
    fake_client.list_apps = AsyncMock(return_value=[{"id": "app-msg", "mode": "chat"}])
    fake_client.get_app_token_costs = AsyncMock(return_value=[
        {"date": "2026-07-04", "token_count": 10, "total_price": "0.001", "currency": "USD"}
    ])
    fake_client.close = AsyncMock()

    with patch("app.services.dify_usage_collector.DifyConsoleClient", return_value=fake_client):
        result = await dify_usage_collector._fetch_app_costs(
            cfg,
            {"app-msg": {"agent_id": "a1"}, "app-missing": {"agent_id": "a2"}},
            "2026-06-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
        )

    assert "app-msg" in result
    assert "app-missing" not in result
    fake_client.get_app_token_costs.assert_awaited_once()  # 只调 app-msg


@pytest.mark.asyncio
async def test_fetch_app_costs_handles_get_app_token_costs_exception(monkeypatch):
    """get_app_token_costs 抛异常 → 该 app 跳过，不影响其他 app。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()
    fake_client = MagicMock()
    fake_client.list_apps = AsyncMock(return_value=[
        {"id": "app-1", "mode": "chat"},
        {"id": "app-2", "mode": "chat"},
    ])
    fake_client.get_app_token_costs = AsyncMock(side_effect=[
        RuntimeError("dify boom"),
        [{"date": "2026-07-04", "token_count": 10, "total_price": "0.5", "currency": "USD"}],
    ])
    fake_client.close = AsyncMock()

    with patch("app.services.dify_usage_collector.DifyConsoleClient", return_value=fake_client):
        result = await dify_usage_collector._fetch_app_costs(
            cfg,
            {"app-1": {"agent_id": "a1"}, "app-2": {"agent_id": "a2"}},
            "2026-06-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
        )

    assert "app-1" not in result
    assert result["app-2"] == {"2026-07-04": 0.5}


# ── _collect_trace_details ────────────────────────────────────────


def test_collect_trace_details_message_trace_extracts_tokens_from_input():
    """message_trace: token 从 trace.input 拿，model 从 (ls_provider, ls_model_name) 归一化为 provider/model。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {
        "app-1": {"agent_id": "agent-aaa", "name": "My Agent", "group_id": "g1"}
    }
    traces = [
        {
            "id": "t1",
            "name": "message",
            "sessionId": "s1",
            "createdAt": "2026-07-04T13:02:44Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-v4-flash",
            },
            "input": {"message_tokens": 8, "answer_tokens": 392, "total_tokens": 400},
        }
    ]
    app_costs = {"app-1": {"2026-07-04": 0.000792}}

    details = _collect_trace_details(agent_meta_map, traces, [], app_costs)
    assert len(details) == 1
    d = details[0]
    assert d.agent_id == "agent-aaa"
    assert d.agent_name == "My Agent"
    assert d.group_id == "g1"
    assert d.dify_app_id == "app-1"
    assert d.trace_id == "t1"
    assert d.session_id == "s1"
    assert d.timestamp == "2026-07-04T13:02:44Z"
    assert d.model == "deepseek/deepseek-v4-flash"  # 归一化为 provider/model
    assert d.prompt_tokens == 8
    assert d.completion_tokens == 392
    assert d.total_tokens == 400
    # 单 trace 当天，cost 不平摊 = 0.000792
    assert d.cost_usd == pytest.approx(0.000792, abs=1e-9)


def test_collect_trace_details_workflow_trace_uses_observations():
    """workflow_trace: 从 GENERATION observation 反查 model + token，model 归一化为 provider/model。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-wf": {"agent_id": "a", "name": "WF", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "workflow",
            "sessionId": "s-wf",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-wf", "total_tokens": 126},
            "input": {"query": "你好"},
        }
    ]
    workflow_observations = [
        {
            "id": "o1",
            "traceId": "t1",
            "type": "GENERATION",
            "model": "deepseek-v4-flash",  # obs.model 不带前缀
            "usage": {"unit": "TOKENS", "input": 5, "output": 121, "total": 126},
            "metadata": {
                "model_provider": "langgenius/deepseek/deepseek",
                "model_name": "deepseek-v4-flash",
            },
        },
    ]
    # workflow 模式 Console API 不返回 total_price，app_costs 无该 app
    app_costs = {}

    details = _collect_trace_details(agent_meta_map, traces, workflow_observations, app_costs)
    assert len(details) == 1
    d = details[0]
    assert d.model == "deepseek/deepseek-v4-flash"  # 从 metadata.model_provider+model_name 归一化
    assert d.prompt_tokens == 5
    assert d.completion_tokens == 121
    assert d.total_tokens == 126
    assert d.cost_usd == 0.0  # workflow 无 cost
    assert d.session_id == "s-wf"


def test_collect_trace_details_workflow_trace_multi_llm_nodes():
    """workflow_trace 有多个 LLM 节点 → 多条 GENERATION observation → 多条 detail。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-wf": {"agent_id": "a", "name": "WF", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "workflow",
            "sessionId": "s-multi",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-wf", "total_tokens": 200},
        }
    ]
    # 2 个 LLM 节点：deepseek + openai
    workflow_observations = [
        {
            "id": "o1",
            "traceId": "t1",
            "type": "GENERATION",
            "model": "deepseek-chat",
            "usage": {"input": 10, "output": 90, "total": 100},
            "metadata": {
                "model_provider": "langgenius/deepseek/deepseek",
                "model_name": "deepseek-chat",
            },
        },
        {
            "id": "o2",
            "traceId": "t1",
            "type": "GENERATION",
            "model": "gpt-4o",
            "usage": {"input": 20, "output": 80, "total": 100},
            "metadata": {
                "model_provider": "langgenius/openai/openai",
                "model_name": "gpt-4o",
            },
        },
        # SPAN 类型 observation 应被跳过
        {
            "id": "o3",
            "traceId": "t1",
            "type": "SPAN",
            "model": None,
            "usage": {"input": 0, "output": 0, "total": 0},
        },
    ]
    app_costs = {}

    details = _collect_trace_details(agent_meta_map, traces, workflow_observations, app_costs)
    assert len(details) == 2  # 只算 GENERATION，SPAN 跳过
    models = sorted(d.model for d in details)
    assert models == ["deepseek/deepseek-chat", "openai/gpt-4o"]  # 归一化带 provider 前缀
    totals = sorted(d.total_tokens for d in details)
    assert totals == [100, 100]
    # workflow trace 拆出的多条 detail 共享同一 sessionId（来自 trace.sessionId）
    assert all(d.session_id == "s-multi" for d in details)


def test_collect_trace_details_workflow_trace_no_observations_fallback():
    """workflow_trace 无 GENERATION observation（异常）→ fallback 用 trace.metadata.total_tokens + model='unknown'。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-wf": {"agent_id": "a", "name": "WF", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "workflow",
            "sessionId": "s-fallback",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-wf", "total_tokens": 126},
        }
    ]
    # 无 observation（Langfuse 拉取失败或延迟）
    workflow_observations = []
    app_costs = {}

    details = _collect_trace_details(agent_meta_map, traces, workflow_observations, app_costs)
    assert len(details) == 1
    d = details[0]
    assert d.model == "unknown"  # fallback
    assert d.prompt_tokens == 0
    assert d.completion_tokens == 126
    assert d.total_tokens == 126
    assert d.cost_usd == 0.0
    assert d.session_id == "s-fallback"


def test_collect_trace_details_workflow_obs_only_for_other_trace_ignored():
    """observation 的 traceId 不匹配任何 workflow trace → 忽略（不会错误关联到 message trace）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {
        "app-msg": {"agent_id": "a", "name": "MSG", "group_id": "g"},
        "app-wf": {"agent_id": "b", "name": "WF", "group_id": "g"},
    }
    traces = [
        {
            "id": "t-msg",
            "name": "message",
            "sessionId": "s-msg",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {
                "app_id": "app-msg",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {"message_tokens": 10, "answer_tokens": 20, "total_tokens": 30},
        },
        {
            "id": "t-wf",
            "name": "workflow",
            "sessionId": "s-wf",
            "createdAt": "2026-07-04T11:00:00Z",
            "metadata": {"app_id": "app-wf", "total_tokens": 50},
        },
    ]
    # observation 关联到 t-wf，不应关联到 t-msg
    workflow_observations = [
        {
            "id": "o1",
            "traceId": "t-wf",
            "type": "GENERATION",
            "model": "deepseek-v4-flash",
            "usage": {"input": 5, "output": 45, "total": 50},
            "metadata": {
                "model_provider": "langgenius/deepseek/deepseek",
                "model_name": "deepseek-v4-flash",
            },
        },
    ]
    details = _collect_trace_details(agent_meta_map, traces, workflow_observations, {})
    assert len(details) == 2
    # message trace 用 ls_provider+ls_model_name 归一化
    msg_detail = next(d for d in details if d.trace_id == "t-msg")
    assert msg_detail.model == "deepseek/deepseek-chat"
    assert msg_detail.prompt_tokens == 10
    assert msg_detail.session_id == "s-msg"
    # workflow trace 用 obs metadata.model_provider+model_name 归一化
    wf_detail = next(d for d in details if d.trace_id == "t-wf")
    assert wf_detail.model == "deepseek/deepseek-v4-flash"
    assert wf_detail.prompt_tokens == 5
    assert wf_detail.session_id == "s-wf"


def test_collect_trace_details_splits_cost_evenly_across_same_day_details():
    """同一 app 同一天的 2 条 message trace → 2 条 detail，cost 按 detail 数平摊。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "sessionId": "s-a",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
        },
        {
            "id": "t2",
            "name": "message",
            "sessionId": "s-b",
            "createdAt": "2026-07-04T11:00:00Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {"message_tokens": 50, "answer_tokens": 50, "total_tokens": 100},
        },
    ]
    # 当天总 cost = 1.0 USD，平摊到 2 条 detail → 每条 0.5
    app_costs = {"app-1": {"2026-07-04": 1.0}}

    details = _collect_trace_details(agent_meta_map, traces, [], app_costs)
    assert len(details) == 2
    assert details[0].cost_usd == pytest.approx(0.5, abs=1e-9)
    assert details[1].cost_usd == pytest.approx(0.5, abs=1e-9)
    # 不同 trace 的 sessionId 各自落到 detail
    sids = sorted(d.session_id for d in details)
    assert sids == ["s-a", "s-b"]


def test_collect_trace_details_workflow_cost_apportioned_across_multi_details():
    """workflow trace 拆成 N 条 detail，cost 按 detail 数平摊（不重复计算）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-wf": {"agent_id": "a", "name": "WF", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "workflow",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-wf"},
        }
    ]
    workflow_observations = [
        {
            "id": "o1",
            "traceId": "t1",
            "type": "GENERATION",
            "model": "deepseek-chat",
            "usage": {"input": 10, "output": 90, "total": 100},
            "metadata": {
                "model_provider": "langgenius/deepseek/deepseek",
                "model_name": "deepseek-chat",
            },
        },
        {
            "id": "o2",
            "traceId": "t1",
            "type": "GENERATION",
            "model": "gpt-4o",
            "usage": {"input": 20, "output": 80, "total": 100},
            "metadata": {
                "model_provider": "langgenius/openai/openai",
                "model_name": "gpt-4o",
            },
        },
    ]
    # 当天总 cost = 0.6 USD（即使是 workflow，假设 Console API 返回了 cost）
    # 平摊到 2 条 detail → 每条 0.3，总和 0.6（不重复）
    app_costs = {"app-wf": {"2026-07-04": 0.6}}

    details = _collect_trace_details(agent_meta_map, traces, workflow_observations, app_costs)
    assert len(details) == 2
    assert details[0].cost_usd == pytest.approx(0.3, abs=1e-9)
    assert details[1].cost_usd == pytest.approx(0.3, abs=1e-9)
    # 总 cost = 0.6，不重复
    assert sum(d.cost_usd for d in details) == pytest.approx(0.6, abs=1e-9)


def test_collect_trace_details_session_id_none_when_trace_has_no_session_id():
    """trace 无 sessionId → detail.session_id is None（不抛异常，activeUsers 自动跳过 None）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            # 无 sessionId 字段
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {"message_tokens": 10, "answer_tokens": 20, "total_tokens": 30},
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert len(details) == 1
    assert details[0].session_id is None


def test_collect_trace_details_skips_trace_without_app_id():
    """无 app_id（Hermes trace）跳过。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"engine_type": "HERMES"},  # 无 app_id
            "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert details == []


def test_collect_trace_details_skips_trace_without_matching_agent():
    """trace.metadata.app_id 不在 agent_meta_map → 跳过（用户：不用管）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-999"},  # 无对应 agent
            "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert details == []


def test_collect_trace_details_skips_trace_without_createdat():
    """trace.createdAt 缺失 → 跳过（无法入 fake log 分桶）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "metadata": {"app_id": "app-1", "ls_model_name": "deepseek-chat"},
            "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert details == []


def test_collect_trace_details_skips_unknown_trace_name():
    """trace.name 非 'message' / 'workflow' → 跳过（暂不处理其他类型）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "unknown_type",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {"app_id": "app-1"},
            "input": {"message_tokens": 100},
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert details == []


def test_collect_trace_details_falls_back_to_zero_when_no_cost_data():
    """app_costs 无该 app 或该日期 → cost=0（降级）。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
        }
    ]
    # app_costs 不含 app-1
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert len(details) == 1
    assert details[0].cost_usd == 0.0


def test_collect_trace_details_handles_missing_token_fields_in_input():
    """trace.input 缺 message_tokens/answer_tokens → token=0，不抛异常。"""
    from app.services.dify_usage_collector import _collect_trace_details

    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    traces = [
        {
            "id": "t1",
            "name": "message",
            "createdAt": "2026-07-04T10:00:00Z",
            "metadata": {
                "app_id": "app-1",
                "ls_provider": "langgenius/deepseek/deepseek",
                "ls_model_name": "deepseek-chat",
            },
            "input": {},  # 全空
        }
    ]
    details = _collect_trace_details(agent_meta_map, traces, [], {})
    assert len(details) == 1
    assert details[0].prompt_tokens == 0
    assert details[0].completion_tokens == 0
    assert details[0].total_tokens == 0


# ── _normalize_dify_model ─────────────────────────────────────────


def test_normalize_dify_model_returns_provider_slash_model():
    """ls_provider 多段路径取最后一段 + model 名拼接成 provider/model。"""
    from app.services.dify_usage_collector import _normalize_dify_model

    assert _normalize_dify_model("langgenius/deepseek/deepseek", "deepseek-chat") == "deepseek/deepseek-chat"
    assert _normalize_dify_model("langgenius/openai/openai", "gpt-4o") == "openai/gpt-4o"


def test_normalize_dify_model_returns_model_only_when_provider_missing():
    """ls_provider 缺失 → 返回 model 原值（不带前缀，避免拼出 /model 这种畸形名）。"""
    from app.services.dify_usage_collector import _normalize_dify_model

    assert _normalize_dify_model(None, "deepseek-chat") == "deepseek-chat"
    assert _normalize_dify_model("", "deepseek-chat") == "deepseek-chat"


def test_normalize_dify_model_returns_unknown_when_model_missing():
    """model 缺失 → 返回 'unknown'。"""
    from app.services.dify_usage_collector import _normalize_dify_model

    assert _normalize_dify_model("langgenius/deepseek/deepseek", None) == "deepseek/unknown"
    assert _normalize_dify_model(None, None) == "unknown"


def test_normalize_dify_model_handles_empty_provider_after_split():
    """provider split 后为空（如 provider='/'）→ 返回 model 原值。"""
    from app.services.dify_usage_collector import _normalize_dify_model

    assert _normalize_dify_model("/", "deepseek-chat") == "deepseek-chat"
    assert _normalize_dify_model("///", "deepseek-chat") == "deepseek-chat"


# ── collect_dify_usage ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_dify_usage_raises_when_langfuse_not_configured():
    """EngineConfig 未配 Langfuse 凭据 + agent_meta_map 非空 → ValueError。"""
    from app.services.dify_usage_collector import collect_dify_usage

    cfg = _make_engine_config(
        langfuse_host=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        langfuse_secret_key_encrypted=None,
    )
    agent_meta_map = {"app-1": {"agent_id": "a", "name": "A", "group_id": "g"}}
    with pytest.raises(ValueError, match="Langfuse"):
        await collect_dify_usage(engine_config=cfg, agent_meta_map=agent_meta_map, days=30)


@pytest.mark.asyncio
async def test_collect_dify_usage_returns_empty_when_agent_meta_map_empty(monkeypatch):
    """agent_meta_map 为空 → 直接返回 []，不调 Langfuse / Dify。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()

    async def _fake_fetch_all_traces(*args, **kwargs):
        raise AssertionError("_fetch_all_traces should not be called")

    async def _fake_fetch_all_observations(*args, **kwargs):
        raise AssertionError("_fetch_all_observations should not be called")

    async def _fake_fetch_app_costs(*args, **kwargs):
        raise AssertionError("_fetch_app_costs should not be called")

    monkeypatch.setattr(dify_usage_collector, "_fetch_all_traces", _fake_fetch_all_traces)
    monkeypatch.setattr(dify_usage_collector, "_fetch_all_observations", _fake_fetch_all_observations)
    monkeypatch.setattr(dify_usage_collector, "_fetch_app_costs", _fake_fetch_app_costs)

    result = await dify_usage_collector.collect_dify_usage(
        engine_config=cfg, agent_meta_map={}, days=30
    )
    assert result == []


@pytest.mark.asyncio
async def test_collect_dify_usage_merges_traces_and_costs(monkeypatch):
    """collect_dify_usage 并发拉 traces + observations + app_costs，按 (app_id, date) 平摊 cost。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()

    async def _fake_fetch_all_traces(from_ts, to_ts, lf_config):
        return [
            {
                "id": "t1",
                "name": "message",
                "createdAt": "2026-07-04T10:00:00Z",
                "metadata": {"app_id": "app-a", "ls_model_name": "deepseek-chat"},
                "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
            },
            {
                "id": "t2",
                "name": "message",
                "createdAt": "2026-07-04T11:00:00Z",
                "metadata": {"app_id": "app-a", "ls_model_name": "deepseek-chat"},
                "input": {"message_tokens": 50, "answer_tokens": 50, "total_tokens": 100},
            },
        ]

    async def _fake_fetch_all_observations(from_ts, to_ts, lf_config):
        return []  # message trace 不依赖 observation

    async def _fake_fetch_app_costs(cfg_, agent_meta_map, from_ts, to_ts):
        return {"app-a": {"2026-07-04": 1.0}}

    monkeypatch.setattr(dify_usage_collector, "_fetch_all_traces", _fake_fetch_all_traces)
    monkeypatch.setattr(dify_usage_collector, "_fetch_all_observations", _fake_fetch_all_observations)
    monkeypatch.setattr(dify_usage_collector, "_fetch_app_costs", _fake_fetch_app_costs)

    agent_meta_map = {"app-a": {"agent_id": "agent-aaa", "name": "A", "group_id": "g1"}}
    result = await dify_usage_collector.collect_dify_usage(
        engine_config=cfg, agent_meta_map=agent_meta_map, days=30
    )

    assert len(result) == 2
    # 平摊：1.0 / 2 = 0.5
    assert all(d.cost_usd == pytest.approx(0.5, abs=1e-9) for d in result)
    # token 累积正确
    total_prompt = sum(d.prompt_tokens for d in result)
    total_completion = sum(d.completion_tokens for d in result)
    assert total_prompt == 150
    assert total_completion == 250


@pytest.mark.asyncio
async def test_collect_dify_usage_workflow_trace_uses_observations(monkeypatch):
    """collect_dify_usage workflow trace 走 observation 反查，每条 GENERATION 一条 detail。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config()

    async def _fake_fetch_all_traces(from_ts, to_ts, lf_config):
        return [
            {
                "id": "t-wf",
                "name": "workflow",
                "createdAt": "2026-07-04T10:00:00Z",
                "metadata": {"app_id": "app-wf", "total_tokens": 126},
            }
        ]

    async def _fake_fetch_all_observations(from_ts, to_ts, lf_config):
        return [
            {
                "id": "o1",
                "traceId": "t-wf",
                "type": "GENERATION",
                "model": "deepseek-v4-flash",
                "usage": {"input": 5, "output": 121, "total": 126},
            },
        ]

    async def _fake_fetch_app_costs(cfg_, agent_meta_map, from_ts, to_ts):
        return {}  # workflow 模式无 cost

    monkeypatch.setattr(dify_usage_collector, "_fetch_all_traces", _fake_fetch_all_traces)
    monkeypatch.setattr(dify_usage_collector, "_fetch_all_observations", _fake_fetch_all_observations)
    monkeypatch.setattr(dify_usage_collector, "_fetch_app_costs", _fake_fetch_app_costs)

    agent_meta_map = {"app-wf": {"agent_id": "a", "name": "WF", "group_id": "g"}}
    result = await dify_usage_collector.collect_dify_usage(
        engine_config=cfg, agent_meta_map=agent_meta_map, days=30
    )
    assert len(result) == 1
    d = result[0]
    assert d.model == "deepseek-v4-flash"  # 不是 "workflow" 占位
    assert d.prompt_tokens == 5
    assert d.completion_tokens == 121
    assert d.total_tokens == 126


@pytest.mark.asyncio
async def test_collect_dify_usage_degrades_when_admin_not_configured(monkeypatch):
    """EngineConfig 未配 admin 账号 → _fetch_app_costs 返回空，cost=0，token 仍正确。"""
    from app.services import dify_usage_collector

    cfg = _make_engine_config(admin_email=None, admin_password=None)

    async def _fake_fetch_all_traces(from_ts, to_ts, lf_config):
        return [
            {
                "id": "t1",
                "name": "message",
                "createdAt": "2026-07-04T10:00:00Z",
                "metadata": {"app_id": "app-a", "ls_model_name": "deepseek-chat"},
                "input": {"message_tokens": 100, "answer_tokens": 200, "total_tokens": 300},
            }
        ]

    async def _fake_fetch_all_observations(from_ts, to_ts, lf_config):
        return []

    async def _fake_fetch_app_costs(cfg_, agent_meta_map, from_ts, to_ts):
        # 真实 _fetch_app_costs 在 admin 未配时返回空 dict
        return {}

    monkeypatch.setattr(dify_usage_collector, "_fetch_all_traces", _fake_fetch_all_traces)
    monkeypatch.setattr(dify_usage_collector, "_fetch_all_observations", _fake_fetch_all_observations)
    monkeypatch.setattr(dify_usage_collector, "_fetch_app_costs", _fake_fetch_app_costs)

    agent_meta_map = {"app-a": {"agent_id": "a", "name": "A", "group_id": "g"}}
    result = await dify_usage_collector.collect_dify_usage(
        engine_config=cfg, agent_meta_map=agent_meta_map, days=30
    )
    assert len(result) == 1
    assert result[0].prompt_tokens == 100
    assert result[0].cost_usd == 0.0


# ── langfuse_client._resolve + list_traces with config ────────────


def test_langfuse_resolve_returns_none_when_config_missing_fields():
    from app.services.langfuse_client import _resolve

    assert _resolve(LangfuseConfig(base_url="", public_key="pk", secret_key="sk")) is None
    assert _resolve(LangfuseConfig(base_url="https://lf", public_key="", secret_key="sk")) is None
    assert _resolve(LangfuseConfig(base_url="https://lf", public_key="pk", secret_key="")) is None


def test_langfuse_resolve_with_config_uses_config_not_global():
    from app.services.langfuse_client import _resolve

    result = _resolve(
        LangfuseConfig(
            base_url="https://lf.example.com/",
            public_key="pk-config",
            secret_key="sk-config",
        )
    )
    assert result is not None
    base, headers = result
    assert base == "https://lf.example.com"
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Content-Type"] == "application/json"


def test_langfuse_resolve_config_none_falls_back_to_global(monkeypatch):
    from app.services.langfuse_client import _resolve
    from app.services import langfuse_client

    monkeypatch.setattr(langfuse_client.settings, "langfuse_base_url", "https://global-lf.example.com")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_public_key", "pk-global")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "sk-global")

    result = _resolve(None)
    assert result is not None
    base, _ = result
    assert base == "https://global-lf.example.com"


def test_langfuse_resolve_config_none_and_global_not_configured(monkeypatch):
    from app.services.langfuse_client import _resolve
    from app.services import langfuse_client

    monkeypatch.setattr(langfuse_client.settings, "langfuse_base_url", "")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_public_key", "")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")
    assert _resolve(None) is None


@pytest.mark.asyncio
async def test_langfuse_list_traces_with_config_passes_metadata_filter(monkeypatch):
    from app.services import langfuse_client

    captured: dict = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "t1"}], "meta": {"page": 1, "totalPages": 1}}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(langfuse_client.httpx, "AsyncClient", _FakeAsyncClient)

    config = LangfuseConfig(
        base_url="https://lf.example.com",
        public_key="pk-test",
        secret_key="sk-test",
    )
    await langfuse_client.list_traces(
        metadata={"app_id": "app-xyz"},
        limit=50,
        offset=0,
        config=config,
    )
    assert captured["url"] == "https://lf.example.com/api/public/traces"
    assert "metadata[app_id]" in captured["params"]
    assert captured["params"]["metadata[app_id]"] == "app-xyz"
    assert captured["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_langfuse_list_traces_returns_none_when_not_configured(monkeypatch):
    from app.services import langfuse_client

    monkeypatch.setattr(langfuse_client.settings, "langfuse_base_url", "")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_public_key", "")
    monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")

    class _SpyAsyncClient:
        def __init__(self, *a, **kw):
            raise AssertionError("httpx 不应被调用")

    monkeypatch.setattr(langfuse_client.httpx, "AsyncClient", _SpyAsyncClient)
    result = await langfuse_client.list_traces()
    assert result is None
