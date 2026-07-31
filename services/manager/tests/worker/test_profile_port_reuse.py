"""_do_create_profile 端口分配单测（port_map.json 单源真相模型）。

端口由 Pod 内 port_map.py alloc 分配（唯一真相），manager 不再读 DB internal_port_map.next_port。
_do_create_profile 调 _port_map_alloc 取端口、调 _port_map_all 读全量作 DB 镜像（去掉 next_port）。
alloc 的幂等/复用/扫描回收语义在 engines/hermes/tests/test_port_map.py 覆盖，此处只验 manager 接线。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_deployment():
    return SimpleNamespace(
        id="dep-1",
        scope_type="ALL",
        scope_target_id=None,
        internal_port_map={"profiles": {}},
        pod_name=None,
    )


def _make_req(profile_name="pn-new"):
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
async def test_do_create_profile_uses_port_map_alloc_and_mirrors_all():
    """port 由 _port_map_alloc 返回；DB 镜像 = _port_map_all 全量（无 next_port）。"""
    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req("pn-new")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    with (
        patch("app.worker.profiles._load_resource_spec", new=AsyncMock(return_value={"max_profiles_per_pod": 20})),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644)) as mock_alloc,
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-new": 8644})),
        patch("app.worker.profiles.get_engine_runtime", new=MagicMock(return_value={"image": "img"})),
        patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock()),
        patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        mock_k8s.update_nginx_config = AsyncMock()
        result = await _do_create_profile(req, db)

    # 端口取自 _port_map_alloc
    assert result["port"] == 8644
    # DB 镜像 = port_map all（无 next_port）
    assert deployment.internal_port_map == {"profiles": {"pn-new": 8644}}
    # alloc 传了正确 profile_name
    mock_alloc.assert_awaited_once()
    assert mock_alloc.call_args.args[1] == "pn-new"


@pytest.mark.asyncio
async def test_do_create_profile_alloc_failure_returns_503():
    """port_map alloc 失败（Pod 重启中/端口耗尽）→ 503，不进入 launch。"""
    from fastapi import HTTPException

    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req("pn-fail")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    with (
        patch("app.worker.profiles._load_resource_spec", new=AsyncMock(return_value={"max_profiles_per_pod": 20})),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        # patch port_map_exec（被真实 port_map_alloc 调用）使其抛异常 → 真实 alloc 捕获并 503
        # port_map_alloc/exec 已迁至 _common，patch 须指向调用方所在命名空间
        patch("app.worker._common.port_map_exec", new=AsyncMock(side_effect=RuntimeError("pod restarting"))),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        with pytest.raises(HTTPException) as exc:
            await _do_create_profile(req, db)
        assert exc.value.status_code == 503
    # alloc 失败 → 不应调 launch / update_nginx
    mock_k8s.update_nginx_config.assert_not_called()


@pytest.mark.asyncio
async def test_do_create_profile_launch_failure_keeps_port():
    """launch 失败不回滚 port_map（保留唯一端口 → 该 profile 502，不串号）。

    验证：launch 抛异常后，_port_map_all 仍被调用、DB 镜像仍写入、nginx 仍更新（死端口路由 → 502 自愈）。
    """
    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req("pn-launchfail")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    with (
        patch("app.worker.profiles._load_resource_spec", new=AsyncMock(return_value={"max_profiles_per_pod": 20})),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8645)),
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-launchfail": 8645})),
        patch("app.worker.profiles.get_engine_runtime", new=MagicMock(return_value={"image": "img"})),
        patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock()),
        patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()),
        patch("app.worker.profiles.k8s_manager") as mock_k8s,
    ):
        # step 4 (profile create) OK，step 5 (launch) 抛异常
        mock_k8s.exec_hermes_command = AsyncMock(side_effect=[None, RuntimeError("launch boom")])
        mock_k8s.update_nginx_config = AsyncMock()
        result = await _do_create_profile(req, db)

    # 端口保留（未回滚），DB 镜像 + nginx 仍写（死端口 → 502 自愈，不串号）
    assert result["port"] == 8645
    assert deployment.internal_port_map == {"profiles": {"pn-launchfail": 8645}}
    mock_k8s.update_nginx_config.assert_awaited_once()
