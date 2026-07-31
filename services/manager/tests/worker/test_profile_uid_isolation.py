"""per-profile UID 隔离 manager 侧接线单测。

验证 _do_create_profile / delete_profile 调用 profile_isolation.py（launch/cleanup），
不再用 chmod 755 / 裸 nohup hermes gateway run。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_do_create_profile_uses_launch_script():
    """create：gateway 启动走 profile_isolation.py launch，不再 chmod 755 / 裸 nohup。"""
    from app.worker.router import CreateProfileRequest, _do_create_profile

    deployment = SimpleNamespace(
        id="dep-1",
        scope_type="ALL",
        scope_target_id=None,
        internal_port_map={"profiles": {}},
        pod_name=None,
    )
    req = CreateProfileRequest(
        agent_id="a" * 32,
        engine_instance_id="p" * 32,
        user_id="u" * 32,
        group_id="g" * 32,
        profile_type="INDEPENDENT",
        profile_name="pn-new",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    with (
        patch(
            "app.worker.profiles._load_resource_spec",
            new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
        ),
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment)),
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644)),
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-new": 8644})),
        patch(
            "app.worker.profiles.get_engine_runtime", new=MagicMock(return_value={"image": "img"})
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

    # 收集所有 exec 命令
    all_cmds: list[str] = []
    for c in mock_k8s.exec_hermes_command.call_args_list:
        all_cmds.extend(c.kwargs.get("commands", []))
    # gateway 启动走 launch 脚本（含 profile 名 + 端口）
    assert any("profile_isolation.py launch pn-new" in cmd for cmd in all_cmds), all_cmds
    assert any("8644" in cmd for cmd in all_cmds)
    # 不再有 chmod 755 或裸 nohup hermes gateway run
    assert not any("chmod 755" in cmd for cmd in all_cmds), all_cmds
    assert not any("nohup hermes gateway run" in cmd for cmd in all_cmds), all_cmds


@pytest.mark.asyncio
async def test_delete_profile_calls_cleanup_userdel():
    """delete：先 profile_isolation.py cleanup（userdel），再 kill/rm。"""
    from app.worker.router import delete_profile

    profile = SimpleNamespace(
        id="pid", profile_name="pn-del", deployment_id="dep-1", instance_id="a" * 32
    )
    deployment = SimpleNamespace(
        id="dep-1",
        scope_type="ALL",
        scope_target_id=None,
        pod_name=None,
        internal_port_map={"profiles": {"pn-del": 8644}, "next_port": 8645},
    )
    profile_result = MagicMock(scalar_one_or_none=MagicMock(return_value=profile))
    dep_result = MagicMock(scalar_one_or_none=MagicMock(return_value=deployment))
    db = AsyncMock()
    # profile 查询 + deployment 查询（+ 兜底额外查询）
    db.execute = AsyncMock(side_effect=[profile_result, dep_result] + [MagicMock()] * 4)
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()

    with patch("app.worker.profiles.k8s_manager") as mock_k8s:
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        mock_k8s.update_nginx_config = AsyncMock()
        await delete_profile("pid", db)

    # 第一组 exec 命令含 cleanup（userdel），且仍含 kill + rm
    first_cmds = mock_k8s.exec_hermes_command.call_args_list[0].kwargs["commands"]
    assert any("profile_isolation.py cleanup pn-del" in c for c in first_cmds), first_cmds
    assert any("kill" in c for c in first_cmds)
    assert any("rm -rf" in c for c in first_cmds)
