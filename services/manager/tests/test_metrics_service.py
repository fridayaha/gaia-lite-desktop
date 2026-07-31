"""metrics_service 纯逻辑单元测试：时间解析、分桶聚合。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import metrics_service as ms


def test_parse_start_time_iso():
    assert ms._parse_start_time("2026-06-19T10:30:00Z") == datetime(
        2026, 6, 19, 10, 30, 0, tzinfo=timezone.utc
    )
    assert ms._parse_start_time("2026-06-19T10:30:00.000+00:00") is not None


def test_parse_start_time_epoch_millis():
    dt = datetime(2026, 6, 19, 10, 30, 0, tzinfo=timezone.utc)
    # 毫秒时间戳
    assert ms._parse_start_time(int(dt.timestamp() * 1000)) == dt
    # 秒时间戳
    assert ms._parse_start_time(int(dt.timestamp())) == dt


def test_parse_start_time_invalid():
    assert ms._parse_start_time(None) is None
    assert ms._parse_start_time("") is None
    assert ms._parse_start_time("not-a-date") is None


def test_extract_logs_variants():
    assert ms._extract_logs({"data": [{"a": 1}]}) == [{"a": 1}]
    assert ms._extract_logs({"logs": [{"b": 2}]}) == [{"b": 2}]
    assert ms._extract_logs([{"c": 3}]) == [{"c": 3}]
    assert ms._extract_logs({}) == []
    assert ms._extract_logs(None) == []


def test_bucketize_aggregates_by_bucket():
    start = datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone.utc)
    bucket = timedelta(minutes=5)
    end = start + timedelta(minutes=15)  # 3 桶

    logs = [
        {"startTime": "2026-06-19T10:01:00Z", "prompt_tokens": 100, "completion_tokens": 20},
        {"startTime": "2026-06-19T10:03:00Z", "prompt_tokens": 50, "completion_tokens": 10},
        {"startTime": "2026-06-19T10:07:00Z", "prompt_tokens": 200, "completion_tokens": 0},
        # 最后一桶为空
    ]
    points = ms._bucketize(logs, start, end, bucket)

    assert len(points) == 3
    assert points[0]["requests"] == 2
    assert points[0]["prompt_tokens"] == 150
    assert points[0]["completion_tokens"] == 30
    assert points[1]["requests"] == 1
    assert points[1]["prompt_tokens"] == 200
    assert points[2]["requests"] == 0


def test_to_metric_points_shape():
    bucketed = [
        {"timestamp": "t1", "requests": 3, "prompt_tokens": 10, "completion_tokens": 2},
        {"timestamp": "t2", "requests": 1, "prompt_tokens": 5, "completion_tokens": 1},
    ]
    pts = ms._to_metric_points(bucketed, "requests")
    assert pts == [{"timestamp": "t1", "value": 3}, {"timestamp": "t2", "value": 1}]


# ── build_instance_overview 测试（conversationCount 数据源切换） ──


def _make_instance(instance_id: str = "inst-1"):
    inst = MagicMock()
    inst.id = instance_id
    inst.litellm_config = {"key": "sk-test"}
    return inst


@pytest.mark.asyncio
async def test_build_instance_overview_uses_langfuse_total_items(monkeypatch):
    """Langfuse 已配置 + 返回 totalItems=23 → conversationCount=23；
    traces 的 distinct sessionId=3 → activeUsers=3（不再用 LiteLLM session_id=27）"""
    instance = _make_instance("inst-1")

    # mock LiteLLM spend_logs 返回 27 行（LLM 调用次数，会高于对话次数）
    async def _fake_fetch_logs(inst, lookback):
        return [{"session_id": f"s{i}", "prompt_tokens": 100, "completion_tokens": 10} for i in range(27)]

    # mock langfuse：5 条 trace，3 个 distinct sessionId（s1/s2/s3），totalItems=23
    async def _fake_list_traces(**_):
        return {
            "data": [
                {"id": "t1", "sessionId": "s1"},
                {"id": "t2", "sessionId": "s1"},  # 同 session
                {"id": "t3", "sessionId": "s2"},
                {"id": "t4", "sessionId": "s3"},
                {"id": "t5", "sessionId": "s3"},  # 同 session
            ],
            "meta": {"totalItems": 23, "totalPages": 1, "page": 1, "limit": 100},
        }

    monkeypatch.setattr(ms, "_fetch_instance_logs", _fake_fetch_logs)
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", _fake_list_traces)

    result = await ms.build_instance_overview(db=None, instance=instance)

    assert result["conversationCount"] == 23  # Langfuse meta.totalItems
    assert result["activeUsers"] == 3  # 3 distinct sessionId（非 27）
    assert result["totalTokens"] == 27 * 110  # 27 行 × (100+10)


@pytest.mark.asyncio
async def test_build_instance_overview_fallback_when_langfuse_not_configured(monkeypatch):
    """Langfuse 未配置 → conversationCount + activeUsers 都 fallback 到 spend_logs"""
    instance = _make_instance()

    async def _fake_fetch_logs(inst, lookback):
        return [{"session_id": f"s{i}", "prompt_tokens": 100, "completion_tokens": 10} for i in range(27)]

    monkeypatch.setattr(ms, "_fetch_instance_logs", _fake_fetch_logs)
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: False)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", AsyncMock())

    result = await ms.build_instance_overview(db=None, instance=instance)

    assert result["conversationCount"] == 27  # fallback 到 spend_logs 行数
    assert result["activeUsers"] == 27  # fallback 到 spend_logs distinct session_id
    # langfuse_client.list_traces 不应被调用
    ms.langfuse_client.list_traces.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_instance_overview_fallback_when_langfuse_returns_none(monkeypatch):
    """Langfuse 配置了但请求失败（返回 None）→ conversationCount + activeUsers 都 fallback"""
    instance = _make_instance()

    async def _fake_fetch_logs(inst, lookback):
        return [{"session_id": f"s{i}", "prompt_tokens": 100, "completion_tokens": 10} for i in range(27)]

    async def _fake_list_traces(**_):
        return None

    monkeypatch.setattr(ms, "_fetch_instance_logs", _fake_fetch_logs)
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", _fake_list_traces)

    result = await ms.build_instance_overview(db=None, instance=instance)

    assert result["conversationCount"] == 27  # fallback
    assert result["activeUsers"] == 27  # fallback


@pytest.mark.asyncio
async def test_build_instance_overview_fallback_when_meta_missing(monkeypatch):
    """Langfuse 返回但 meta.totalItems 缺失 → conversationCount fallback 到 len(logs)；
    但 data 有 sessionId → activeUsers 仍用 Langfuse distinct sessionId（有数据就用）"""
    instance = _make_instance()

    async def _fake_fetch_logs(inst, lookback):
        return [{"session_id": "s1", "prompt_tokens": 0, "completion_tokens": 0} for _ in range(5)]

    async def _fake_list_traces(**_):
        return {"data": [{"id": "t1", "sessionId": "a1"}, {"id": "t2", "sessionId": "a2"}]}  # 没 meta

    monkeypatch.setattr(ms, "_fetch_instance_logs", _fake_fetch_logs)
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", _fake_list_traces)

    result = await ms.build_instance_overview(db=None, instance=instance)

    assert result["conversationCount"] == 5  # meta 缺失，fallback 到 len(logs)
    assert result["activeUsers"] == 2  # data 有 2 个 distinct sessionId


@pytest.mark.asyncio
async def test_build_instance_overview_active_users_fallback_when_no_session_id(monkeypatch):
    """Langfuse 返回 traces 但无 sessionId 字段 → activeUsers fallback 到 spend_logs distinct"""
    instance = _make_instance()

    async def _fake_fetch_logs(inst, lookback):
        return [{"session_id": f"s{i}", "prompt_tokens": 100, "completion_tokens": 10} for i in range(5)]

    async def _fake_list_traces(**_):
        return {
            "data": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],  # 无 sessionId
            "meta": {"totalItems": 3, "totalPages": 1, "page": 1, "limit": 100},
        }

    monkeypatch.setattr(ms, "_fetch_instance_logs", _fake_fetch_logs)
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", _fake_list_traces)

    result = await ms.build_instance_overview(db=None, instance=instance)

    assert result["conversationCount"] == 3  # meta.totalItems
    assert result["activeUsers"] == 5  # fallback 到 spend_logs distinct session_id


# ── build_instance_overview Dify 分支测试 ──


def _make_dify_instance(instance_id: str = "dify-1"):
    """构造 Dify 外接实例 mock。"""
    inst = MagicMock()
    inst.id = instance_id
    inst.name = "Dify Agent"
    inst.group_id = None
    inst.engine_type = "DIFY"
    inst.dify_config = {
        "app_id": "app-1",
        "base_url": "http://dify.example.com",
        "app_type": "chat",
    }
    return inst


class _FakeResult:
    """模拟 SQLAlchemy db.execute() 返回，scalars().first() 取首个 EngineConfig。"""

    def __init__(self, ec=None):
        self._ec = ec

    def scalars(self):
        class _S:
            def __init__(self, ec):
                self.ec = ec

            def first(self):
                return self.ec

            def all(self):
                return [self.ec] if self.ec else []

        return _S(self._ec)


class _FakeEC:
    """模拟 EngineConfig。"""

    def __init__(self, has_langfuse=True):
        self.id = "ec-1"
        self.engine_type = "DIFY"
        self.base_url = "http://dify.example.com"
        self.langfuse_host = "http://lf" if has_langfuse else None
        self.langfuse_public_key = "pk-xxx" if has_langfuse else None
        self.langfuse_secret_key_encrypted = "enc-xxx" if has_langfuse else None


@pytest.mark.asyncio
async def test_build_instance_overview_dify_uses_collect_dify_usage(monkeypatch):
    """Dify 外接实例 → 走 _build_dify_instance_overview，从 collect_dify_usage 拿 details 算 4 卡。"""
    from app.services.dify_usage_collector import DifyTraceDetail

    instance = _make_dify_instance()
    ec = _FakeEC(has_langfuse=True)

    async def _fake_db_execute(stmt):
        return _FakeResult(ec)

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    # mock build_langfuse_config 返回非 None（视为已配齐 Langfuse）
    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())
    # mock collect_dify_usage 返回 3 条 detail，2 个 distinct session_id
    async def _fake_collect(ec_obj, agent_meta_map, days=30):
        return [
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t1", session_id="s1",
                timestamp="2026-07-04T10:00:00Z", model="deepseek/deepseek-chat",
                prompt_tokens=100, completion_tokens=50, total_tokens=150, cost_usd=0.01,
            ),
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t2", session_id="s1",  # 同 session
                timestamp="2026-07-04T11:00:00Z", model="deepseek/deepseek-chat",
                prompt_tokens=80, completion_tokens=40, total_tokens=120, cost_usd=0.008,
            ),
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t3", session_id="s2",
                timestamp="2026-07-04T12:00:00Z", model="deepseek/deepseek-chat",
                prompt_tokens=200, completion_tokens=100, total_tokens=300, cost_usd=0.02,
            ),
        ]
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    # Hermes 路径的 mock 不应被调用
    monkeypatch.setattr(ms, "_fetch_instance_logs", AsyncMock())
    monkeypatch.setattr(ms.langfuse_client, "is_configured", lambda: False)
    monkeypatch.setattr(ms.langfuse_client, "list_traces", AsyncMock())

    result = await ms.build_instance_overview(db=_FakeDB(), instance=instance)

    assert result["conversationCount"] == 3  # len(details)
    assert result["totalTokens"] == 150 + 120 + 300  # Σ total_tokens
    assert result["activeUsers"] == 2  # distinct session_id = {s1, s2}
    assert isinstance(result["conversationTrend"], list)
    # 趋势图 7d，按天分桶
    assert len(result["conversationTrend"]) == 7
    # Hermes 路径不应被调用
    ms._fetch_instance_logs.assert_not_awaited()
    ms.langfuse_client.list_traces.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_instance_overview_dify_no_engine_config_returns_zero(monkeypatch):
    """Dify 实例但按 base_url 找不到 EngineConfig → 4 卡全 0（不抛异常）。"""
    instance = _make_dify_instance()

    async def _fake_db_execute(stmt):
        return _FakeResult(None)  # 无 EngineConfig

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())
    async def _fake_collect(*a, **kw):
        raise AssertionError("collect_dify_usage 不应被调用")
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    result = await ms.build_instance_overview(db=_FakeDB(), instance=instance)

    assert result == {
        "conversationCount": 0,
        "totalTokens": 0,
        "activeUsers": 0,
        "conversationTrend": [],
    }


@pytest.mark.asyncio
async def test_build_instance_overview_dify_collect_raises_returns_zero(monkeypatch):
    """collect_dify_usage raise → 4 卡全 0（不抛异常到上层）。"""
    instance = _make_dify_instance()
    ec = _FakeEC(has_langfuse=True)

    async def _fake_db_execute(stmt):
        return _FakeResult(ec)

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())
    async def _fake_collect(*a, **kw):
        raise RuntimeError("Langfuse down")
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    result = await ms.build_instance_overview(db=_FakeDB(), instance=instance)

    assert result["conversationCount"] == 0
    assert result["totalTokens"] == 0
    assert result["activeUsers"] == 0
    assert result["conversationTrend"] == []


# ── build_instance_metrics Dify 分支测试 ──


@pytest.mark.asyncio
async def test_build_instance_metrics_dify_uses_collect_dify_usage(monkeypatch):
    """Dify 外接实例 → 走 _build_dify_instance_metrics，requests/tokens 从 details 分桶。"""
    from app.services.dify_usage_collector import DifyTraceDetail

    instance = _make_dify_instance()
    ec = _FakeEC(has_langfuse=True)

    async def _fake_db_execute(stmt):
        return _FakeResult(ec)

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())

    # 3 条 trace，timestamps 在最近 24h 内（动态生成，测试稳定）
    now = datetime.now(timezone.utc)
    ts1 = (now - timedelta(hours=2)).isoformat()
    ts2 = (now - timedelta(hours=3)).isoformat()
    ts3 = (now - timedelta(hours=5)).isoformat()

    async def _fake_collect(ec_obj, agent_meta_map, days=1):
        return [
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t1", session_id="s1",
                timestamp=ts1, model="deepseek/deepseek-chat",
                prompt_tokens=100, completion_tokens=50, total_tokens=150, cost_usd=0.01,
            ),
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t2", session_id="s1",
                timestamp=ts2, model="deepseek/deepseek-chat",
                prompt_tokens=80, completion_tokens=40, total_tokens=120, cost_usd=0.008,
            ),
            DifyTraceDetail(
                agent_id="dify-1", agent_name="Dify Agent", group_id="",
                dify_app_id="app-1", trace_id="t3", session_id="s2",
                timestamp=ts3, model="deepseek/deepseek-chat",
                prompt_tokens=200, completion_tokens=100, total_tokens=300, cost_usd=0.02,
            ),
        ]
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    # Hermes 路径的 mock 不应被调用
    monkeypatch.setattr(ms, "_fetch_instance_logs", AsyncMock())
    monkeypatch.setattr(ms, "_resource_history", AsyncMock(return_value=([], [])))

    result = await ms.build_instance_metrics(
        db=_FakeDB(), instance=instance, range_key="24h"
    )

    # Dify 分支：无 Pod → cpu/memory 必为 []，resourceRequest 全 0
    assert result["cpu"] == []
    assert result["memory"] == []
    assert result["resourceRequest"] == {"cpu_m": 0, "memory_mi": 0}

    # 24h range + 1h bucket → 24 个桶
    assert len(result["requests"]) == 24
    # Σ requests = 3（24h 内共 3 条 trace，全在桶内）
    assert sum(p["value"] for p in result["requests"]) == 3
    # tokens.input: Σ prompt_tokens = 100 + 80 + 200 = 380
    assert sum(p["value"] for p in result["tokens"]["input"]) == 380
    # tokens.output: Σ completion_tokens = 50 + 40 + 100 = 190
    assert sum(p["value"] for p in result["tokens"]["output"]) == 190
    # attribution
    assert result["attribution"]["logsFetched"] == 3
    assert result["attribution"]["keyPresent"] is False
    assert result["attribution"]["sharedAgentCount"] == 0

    # Hermes 路径不应被调用
    ms._fetch_instance_logs.assert_not_awaited()
    ms._resource_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_instance_metrics_dify_no_engine_config_returns_empty(monkeypatch):
    """Dify 实例但按 base_url 找不到 EngineConfig → 全空（不抛异常）。"""
    instance = _make_dify_instance()

    async def _fake_db_execute(stmt):
        return _FakeResult(None)  # 无 EngineConfig

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())
    async def _fake_collect(*a, **kw):
        raise AssertionError("collect_dify_usage 不应被调用")
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    result = await ms.build_instance_metrics(
        db=_FakeDB(), instance=instance, range_key="24h"
    )

    assert result["cpu"] == []
    assert result["memory"] == []
    assert result["requests"] == []
    assert result["tokens"]["input"] == []
    assert result["tokens"]["output"] == []
    assert result["resourceRequest"] == {"cpu_m": 0, "memory_mi": 0}
    assert result["attribution"]["logsFetched"] == 0
    assert result["attribution"]["keyPresent"] is False


@pytest.mark.asyncio
async def test_build_instance_metrics_dify_collect_raises_returns_empty(monkeypatch):
    """collect_dify_usage raise → 全空（不抛异常到上层）。"""
    instance = _make_dify_instance()
    ec = _FakeEC(has_langfuse=True)

    async def _fake_db_execute(stmt):
        return _FakeResult(ec)

    class _FakeDB:
        async def execute(self, stmt):
            return await _fake_db_execute(stmt)

    monkeypatch.setattr(ms, "build_langfuse_config", lambda e: object())
    async def _fake_collect(*a, **kw):
        raise RuntimeError("Langfuse down")
    monkeypatch.setattr(ms, "collect_dify_usage", _fake_collect)

    result = await ms.build_instance_metrics(
        db=_FakeDB(), instance=instance, range_key="24h"
    )

    assert result["cpu"] == []
    assert result["memory"] == []
    assert result["requests"] == []
    assert result["tokens"]["input"] == []
    assert result["tokens"]["output"] == []
    assert result["attribution"]["logsFetched"] == 0

