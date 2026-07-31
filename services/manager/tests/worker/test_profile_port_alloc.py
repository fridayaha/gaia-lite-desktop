"""port_map.json 单源真相模型下的端口分配专项测试。

覆盖 test_profile_port_reuse.py 之外的并发锁与 ensure 真相同步：
- _do_create_profile 在 alloc 前获取 deployment 级 advisory lock（防并发 alloc 撞端口）
- ensure_profile 以 port_map.json get 为真相同步 DB internal_port（DB stale 时对齐）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_deployment(internal_port_map=None):
    return SimpleNamespace(
        id="dep-lock-1",
        scope_type="ALL",
        scope_target_id=None,
        internal_port_map=internal_port_map or {"profiles": {}},
        pod_name="pod-x",
    )


def _make_req(profile_name="pn-x"):
    from app.worker.router import CreateProfileRequest

    return CreateProfileRequest(
        agent_id="a" * 32,
        engine_instance_id="p" * 32,
        user_id="u" * 32,
        group_id="g" * 32,
        profile_type="INDEPENDENT",
        profile_name=profile_name,
    )


@pytest.mark.asyncio
async def test_do_create_profile_acquires_deployment_advisory_lock():
    """alloc 前必须获取 deployment 级 advisory lock（序列化同 Pod 端口分配）。"""
    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req("pn-lock")
    db = AsyncMock()
    # 记录所有 execute 的 SQL 文本
    executed_sql: list[str] = []

    async def _exec(stmt, *a, **k):
        s = str(stmt)
        executed_sql.append(s)
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    db.execute = _exec
    db.add = MagicMock()
    db.commit = AsyncMock()

    with (
        patch(
            "app.worker.profiles._load_resource_spec",
            new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
        ),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644)),
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-lock": 8644})),
        patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock()),
        patch("app.worker.profiles._seed_persona", new=AsyncMock()),
        patch("app.worker.profiles._load_instance_config", new=AsyncMock(return_value={})),
        patch("app.worker.profiles._ensure_shared_skill_dir", new=AsyncMock()),
        patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        mock_k8s.update_nginx_config = AsyncMock()
        await _do_create_profile(req, db)

    # 含 advisory lock 语句（值经 :did 绑定参数传入，键为 deployment.id）
    assert any("pg_advisory_xact_lock" in s for s in executed_sql), executed_sql


@pytest.mark.asyncio
async def test_ensure_profile_syncs_port_map_truth_to_db():
    """DB internal_port stale（8644）但 port_map.json 真相为 8645 → ensure 对齐 DB 到 8645。"""
    from app.worker.router import CreateProfileRequest, ensure_profile

    profile = SimpleNamespace(
        id="prof-1",
        profile_name="pn-sync",
        deployment_id="dep-sync-1",
        instance_id="a" * 32,
        internal_port=8644,  # DB stale
    )
    deployment = SimpleNamespace(
        id="dep-sync-1",
        scope_type="ALL",
        scope_target_id=None,
        pod_name="pod-x",
    )
    req = CreateProfileRequest(
        agent_id="a" * 32,
        engine_instance_id="p" * 32,
        user_id="u" * 32,
        group_id="g" * 32,
        profile_type="INDEPENDENT",
        profile_name="pn-sync",
    )
    db = AsyncMock()
    db.commit = AsyncMock()

    # execute 顺序：1) profile select → profile  2) deployment select → deployment
    #               3) UPDATE agent_profiles  4) _heal _load_instance_config  5) UPDATE agent_deployments
    update_calls: list[str] = []
    _row_profile = MagicMock(scalar_one_or_none=MagicMock(return_value=profile))
    _row_dep = MagicMock(scalar_one_or_none=MagicMock(return_value=deployment))
    _seq = [_row_profile, _row_dep]

    async def _exec(stmt, *a, **k):
        if _seq:
            return _seq.pop(0)
        s = str(stmt).lower()
        if "update agent_profiles" in s:
            update_calls.append("agent_profiles")
        if "update agent_deployments" in s:
            update_calls.append("agent_deployments")
        return MagicMock()

    db.execute = _exec

    with (
        patch("app.worker.profiles._port_map_exec", new=AsyncMock(return_value="8645")),  # 真相
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-sync": 8645})),
        patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock()),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        # 目录检查 + 健康探测 exec：返回含 EXISTS + 200（alive，不重启）
        mock_k8s.exec_hermes_command = AsyncMock(return_value="EXISTS 200")
        mock_k8s.update_nginx_config = AsyncMock()
        result = await ensure_profile(req, db)

    # 真相 8645 != DB 8644 → UPDATE agent_profiles internal_port=8645
    assert "agent_profiles" in update_calls, update_calls
    # 返回的 port 为真相
    assert result["port"] == 8645
    # profile.internal_port 已对齐
    assert profile.internal_port == 8645
    # nginx 从 port_map all 刷新 + DB 镜像同步
    assert "agent_deployments" in update_calls, update_calls
    mock_k8s.update_nginx_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_port_map_mirror_preserves_browsers():
    """sync_deployment_port_map_mirror 仅更新 profiles，保留 browsers（VNC 接管依赖）。

    回归：旧实现整体赋 {"profiles": ...} 抹掉 browsers → 每次 ensure（每条消息）清空
    browser Pod 记录 → gateway resolve_browser_target 抛 ProfileNotFound → VNC 接管 403。
    """
    import json as _json

    from app.worker._common import sync_deployment_port_map_mirror

    existing = {
        "profiles": {"pn-old": 8644},
        "browsers": {"pn-old": {"pod": "browser-x", "vnc_pw": "secret"}},
    }
    captured: dict = {}

    async def _exec(stmt, *a, **k):
        s = str(stmt).lower()
        params = a[0] if a else k
        if "select internal_port_map" in s:
            return MagicMock(fetchone=MagicMock(return_value=(existing,)))
        if "update agent_deployments" in s:
            captured["pm"] = params.get("pm")
            captured["did"] = params.get("did")
        return MagicMock()

    db = AsyncMock()
    db.execute = _exec

    await sync_deployment_port_map_mirror(db, "dep-1", {"pn-new": 8645})

    merged = _json.loads(captured["pm"])
    # profiles 按真相更新
    assert merged["profiles"] == {"pn-new": 8645}
    # browsers 保留（不抹掉）
    assert merged["browsers"] == {"pn-old": {"pod": "browser-x", "vnc_pw": "secret"}}
    assert captured["did"] == "dep-1"


@pytest.mark.asyncio
async def test_do_create_profile_port_map_preserves_existing_browsers():
    """_do_create_profile 写 profiles 子键时保留同 deployment 其他 profile 的 browsers。

    回归：旧 `internal_port_map = {"profiles": ...}` 整体覆盖会抹掉 browsers。
    """
    from app.worker.router import _do_create_profile

    deployment = _make_deployment(
        internal_port_map={
            "profiles": {"other-prof": 8644},
            "browsers": {"other-prof": {"pod": "browser-y", "vnc_pw": "pw"}},
        }
    )
    req = _make_req("pn-new")
    db = AsyncMock()

    async def _exec(stmt, *a, **k):
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    db.execute = _exec
    db.add = MagicMock()
    db.commit = AsyncMock()

    with (
        patch(
            "app.worker.profiles._load_resource_spec",
            new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
        ),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8645)),
        patch(
            "app.worker.profiles._port_map_all",
            new=AsyncMock(return_value={"other-prof": 8644, "pn-new": 8645}),
        ),
        patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock()),
        patch("app.worker.profiles._seed_persona", new=AsyncMock()),
        patch("app.worker.profiles._load_instance_config", new=AsyncMock(return_value={})),
        patch("app.worker.profiles._ensure_shared_skill_dir", new=AsyncMock()),
        patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        mock_k8s.update_nginx_config = AsyncMock()
        await _do_create_profile(req, db)

    # profiles 按真相更新，browsers 保留（未被整体覆盖抹掉）
    pm = deployment.internal_port_map
    assert pm["profiles"] == {"other-prof": 8644, "pn-new": 8645}
    assert pm["browsers"] == {"other-prof": {"pod": "browser-y", "vnc_pw": "pw"}}
