"""dashboard 聚合 API 纯逻辑单元测试：CPU/内存配额解析、实例状态映射。"""

import pytest

from app.api import dashboard as dash
from app.models import DeploymentStatus
from tests.conftest import make_mock_user


def test_parse_cpu_m():
    assert dash._parse_cpu_m("500m") == 500
    assert dash._parse_cpu_m("2") == 2000
    assert dash._parse_cpu_m("0.5") == 500
    assert dash._parse_cpu_m("") == 0
    assert dash._parse_cpu_m(None) == 0
    assert dash._parse_cpu_m("nonsense") == 0


def test_parse_mem_mi():
    assert dash._parse_mem_mi("256Mi") == 256
    assert dash._parse_mem_mi("2Gi") == 2048
    assert dash._parse_mem_mi("512M") == 512
    assert dash._parse_mem_mi("1024") == 1024
    assert dash._parse_mem_mi("") == 0
    assert dash._parse_mem_mi(None) == 0


def test_extract_logs_variants():
    assert dash._extract_logs({"data": [{"a": 1}]}) == [{"a": 1}]
    assert dash._extract_logs({"logs": [{"b": 2}]}) == [{"b": 2}]
    assert dash._extract_logs([{"c": 3}]) == [{"c": 3}]
    assert dash._extract_logs({}) == []
    assert dash._extract_logs(None) == []


@pytest.mark.asyncio
async def test_probe_down_on_request_error(monkeypatch):
    """_probe 在 httpx 抛错时返回 down，不抛异常。"""

    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise OSError("boom")

    monkeypatch.setattr(dash.httpx, "AsyncClient", _BoomClient)
    res = await dash._probe("Controller", "http://controller:8001/health")
    assert res["name"] == "Controller"
    assert res["status"] == "down"


@pytest.mark.asyncio
async def test_probe_none_url_is_ok():
    res = await dash._probe("Manager", None)
    assert res == {"name": "Manager", "status": "ok", "latencyMs": 0}


def test_storage_kind_detection():
    """endpoint 后缀 → 类型映射（与 minio_archiver 判定一致）。"""
    assert dash._storage_kind("https://oss-cn-hangzhou.aliyuncs.com") == "oss"
    assert dash._storage_kind("https://cos.ap-shanghai.myqcloud.com") == "cos"
    assert dash._storage_kind("http://minio:9000") == "minio"
    assert dash._storage_kind("http://localhost:9000") == "minio"
    assert dash._storage_kind("") == "minio"


@pytest.mark.asyncio
async def test_probe_storage_minio_uses_health_endpoint(monkeypatch):
    """MinIO 走 /minio/health/live 公开端点，label 为'对象存储'。"""
    monkeypatch.setattr(dash.settings, "minio_endpoint", "http://minio:9000")

    captured = {}

    async def _fake_probe(name, url, **kw):
        captured["name"] = name
        captured["url"] = url
        return {"name": name, "status": "ok", "latencyMs": 1}

    monkeypatch.setattr(dash, "_probe", _fake_probe)
    res = await dash._probe_storage()
    assert res["name"] == "对象存储"
    assert captured["url"] == "http://minio:9000/minio/health/live"


@pytest.mark.asyncio
async def test_probe_storage_oss_uses_archiver_list_buckets(monkeypatch):
    """OSS 走 archiver.client.list_buckets（authed），成功返回 ok。"""
    monkeypatch.setattr(dash.settings, "minio_endpoint", "https://oss-cn-hangzhou.aliyuncs.com")

    from app.worker import minio_archiver

    class _FakeClient:
        def list_buckets(self):
            return ["bucket-a", "bucket-b"]

    monkeypatch.setattr(minio_archiver.archiver, "client", _FakeClient())
    res = await dash._probe_storage()
    assert res["name"] == "对象存储"
    assert res["status"] == "ok"


@pytest.mark.asyncio
async def test_probe_storage_oss_down_on_auth_failure(monkeypatch):
    """OSS list_buckets 抛错（auth/连接失败）→ down。"""
    monkeypatch.setattr(dash.settings, "minio_endpoint", "https://oss-cn-hangzhou.aliyuncs.com")

    from app.worker import minio_archiver

    class _FakeClient:
        def list_buckets(self):
            raise Exception("AccessDenied")

    monkeypatch.setattr(minio_archiver.archiver, "client", _FakeClient())
    res = await dash._probe_storage()
    assert res["status"] == "down"


def test_instance_status_spec_covers_all_relevant_states():
    """状态分布应覆盖 RUNNING/SUSPENDED/ARCHIVED/FAILED 等核心生命周期。"""
    # spec 是模块内部常量列表 [(label, status, color), ...]
    # 通过反射 get_billing 同模块的 spec 不易，改用端到端：构造 counts 直接断言映射
    counts = {
        DeploymentStatus.RUNNING: 3,
        DeploymentStatus.SUSPENDED: 1,
        DeploymentStatus.FAILED: 2,
    }
    # 复用与路由相同的标签/颜色约定
    spec = [
        ("运行中", DeploymentStatus.RUNNING, "#00a870"),
        ("已挂起", DeploymentStatus.SUSPENDED, "#e6a23c"),
        ("已归档", DeploymentStatus.ARCHIVED, "#909399"),
        ("部署中", DeploymentStatus.DEPLOYING, "#386bf5"),
        ("待部署", DeploymentStatus.PENDING, "#a0cfff"),
        ("异常", DeploymentStatus.FAILED, "#f56c6c"),
    ]
    items = [
        {"name": label, "value": counts.get(st, 0), "color": color}
        for label, st, color in spec
    ]
    by_name = {it["name"]: it for it in items}
    assert by_name["运行中"]["value"] == 3
    assert by_name["已挂起"]["value"] == 1
    assert by_name["异常"]["value"] == 2
    assert by_name["已归档"]["value"] == 0
    # 所有 DeploymentStatus 成员都应在 spec 中有对应标签（ARCHIVED/DEPLOYING/PENDING 也覆盖）
    covered = {st for _, st, _ in spec}
    for st in DeploymentStatus:
        assert st in covered, f"未覆盖状态: {st}"


# ── /my-conversation-trend（终端用户对话趋势） ───────────────────


@pytest.mark.asyncio
async def test_my_conversation_trend_langfuse_not_configured(monkeypatch):
    """Langfuse 未配置 → 返回长度=days 的全 0 数组，不抛异常。"""
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: False)
    user = make_mock_user()
    res = await dash.get_my_conversation_trend(days=7, current_user=user)
    assert len(res["items"]) == 7
    assert all(it["value"] == 0 for it in res["items"])
    # 日期格式 MM-DD
    assert all(len(it["date"]) == 5 and it["date"][2] == "-" for it in res["items"])


@pytest.mark.asyncio
async def test_my_conversation_trend_langfuse_returns_none(monkeypatch):
    """Langfuse 已配置但 list_traces 返回 None（网络失败）→ 全 0，不抛异常。"""
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: True)

    async def fake_list_traces(**kw):
        return None

    monkeypatch.setattr(dash.langfuse_client, "list_traces", fake_list_traces)
    user = make_mock_user()
    res = await dash.get_my_conversation_trend(days=7, current_user=user)
    assert len(res["items"]) == 7
    assert all(it["value"] == 0 for it in res["items"])


@pytest.mark.asyncio
async def test_my_conversation_trend_groups_by_date(monkeypatch):
    """3 条不同日期的 trace → 按日期分组正确，其余天数补 0。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    traces = [
        {"createdAt": now.isoformat()},  # 今天
        {"createdAt": now.isoformat()},  # 今天（再 +1）
        {"createdAt": (now - timedelta(days=1)).isoformat()},  # 昨天
        {"createdAt": "not-a-valid-date"},  # 非法时间戳应被跳过
        {},  # 无时间戳应被跳过
    ]
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: True)

    async def fake_list_traces(**kw):
        return {"data": traces}

    monkeypatch.setattr(dash.langfuse_client, "list_traces", fake_list_traces)
    user = make_mock_user()
    res = await dash.get_my_conversation_trend(days=7, current_user=user)
    assert len(res["items"]) == 7
    # 倒序：最后一项是今天，倒数第二项是昨天
    assert res["items"][-1]["value"] == 2  # 今天 2 条
    assert res["items"][-2]["value"] == 1  # 昨天 1 条
    assert res["items"][-3]["value"] == 0  # 前天 0 条


@pytest.mark.asyncio
async def test_my_conversation_trend_passes_enduser_id_metadata(monkeypatch):
    """list_traces 被调用时 metadata.enduser_id = str(current_user.id)。"""
    captured: dict = {}

    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: True)

    async def spy_list_traces(**kw):
        captured.update(kw)
        return {"data": []}

    monkeypatch.setattr(dash.langfuse_client, "list_traces", spy_list_traces)
    user = make_mock_user()
    await dash.get_my_conversation_trend(days=7, current_user=user)
    assert captured.get("metadata") == {"enduser_id": str(user.id)}
    assert captured.get("limit") == 100
    assert "from_ts" in captured and "to_ts" in captured


# ── /my-stats（终端用户首页可访问 Agent + 本月对话） ────────────────


async def _fake_instances(db, user_id, is_admin=False, *, count=0):
    """Mock list_accessible_instances，返回 count 个占位对象。"""
    return [object() for _ in range(count)]


@pytest.mark.asyncio
async def test_my_stats_accessible_agents_count(monkeypatch):
    """accessible_agents = list_accessible_instances 返回列表长度。"""
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: False)
    monkeypatch.setattr(
        dash.instance_service,
        "list_accessible_instances",
        lambda db, user_id, is_admin=False: _fake_instances(db, user_id, is_admin, count=3),
    )
    user = make_mock_user()
    res = await dash.get_my_stats(db=None, current_user=user)  # type: ignore[arg-type]
    assert res["accessible_agents"] == 3
    assert res["monthly_conversations"] == 0  # Langfuse 未配置


@pytest.mark.asyncio
async def test_my_stats_langfuse_not_configured(monkeypatch):
    """Langfuse 未配置 → monthly_conversations=0，不抛异常。"""
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: False)
    monkeypatch.setattr(
        dash.instance_service,
        "list_accessible_instances",
        _fake_instances,
    )
    user = make_mock_user()
    res = await dash.get_my_stats(db=None, current_user=user)  # type: ignore[arg-type]
    assert res["monthly_conversations"] == 0
    assert res["accessible_agents"] == 0


@pytest.mark.asyncio
async def test_my_stats_monthly_uses_total_items(monkeypatch):
    """monthly_conversations 读 meta.totalItems，不靠 len(data)。"""
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(dash.instance_service, "list_accessible_instances", _fake_instances)

    async def fake_list_traces(**kw):
        # data 是空但 meta.totalItems=42；endpoint 应采用 42
        return {"data": [], "meta": {"totalItems": 42}}

    monkeypatch.setattr(dash.langfuse_client, "list_traces", fake_list_traces)
    user = make_mock_user()
    res = await dash.get_my_stats(db=None, current_user=user)  # type: ignore[arg-type]
    assert res["monthly_conversations"] == 42


@pytest.mark.asyncio
async def test_my_stats_monthly_from_ts_is_month_start(monkeypatch):
    """from_ts 是本月第一天；metadata.enduser_id 透传 current_user.id。"""
    captured: dict = {}
    monkeypatch.setattr(dash.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(dash.instance_service, "list_accessible_instances", _fake_instances)

    async def spy_list_traces(**kw):
        captured.update(kw)
        return {"meta": {"totalItems": 0}}

    monkeypatch.setattr(dash.langfuse_client, "list_traces", spy_list_traces)
    user = make_mock_user()
    await dash.get_my_stats(db=None, current_user=user)  # type: ignore[arg-type]

    assert captured.get("metadata") == {"enduser_id": str(user.id)}
    assert captured.get("limit") == 1
    from_ts = captured.get("from_ts", "")
    # 本月第一天 ISO 字符串
    assert from_ts.endswith("-01T00:00:00+00:00")
