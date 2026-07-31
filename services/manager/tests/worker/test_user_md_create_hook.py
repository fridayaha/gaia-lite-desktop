"""_do_create_profile 在新建 profile 时挂后台 seed 任务（SOUL.md/skills）的 hook 测试。

seeds 已从 _do_create_profile 同步路径移到 _schedule_profile_seeds 后台任务（避免
~10 次串行 exec 超 gateway 10s 超时留 root 半成品）。本测试只验 _do_create_profile
在 commit 后以正确参数触发 _schedule_profile_seeds；seed 主体（_run_profile_seeds）
的顺序/跳过/异常由 test_profile_seeds_background.py 覆盖。
"""

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.worker.router import CreateProfileRequest, _do_create_profile


class _FakeDeployment:
    def __init__(self):
        self.id = uuid.uuid4()
        self.scope_type = "ALL"
        self.scope_target_id = None
        self.pod_name = "pod-x"
        self.internal_port_map = {}


def _req(profile_type: str) -> CreateProfileRequest:
    return CreateProfileRequest(
        agent_id=str(uuid.uuid4()),
        engine_instance_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        group_id=str(uuid.uuid4()),
        profile_type=profile_type,
        profile_name=f"prof-{profile_type.lower()}",
    )


class TestCreateProfileHook:
    """mock _do_create_profile 内部依赖，仅观测 _schedule_profile_seeds hook 调用。"""

    @pytest.fixture(autouse=True)
    def _stub_port_map(self):
        """port_map.py 在 Pod 内执行，单测旁路（分配端口 + 读全量镜像）。"""
        with (
            patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644)),
            patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={})),
        ):
            yield

    async def test_independent_schedules_seeds_with_user_id(self, mock_k8s):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()  # db.add 同步调用
        req = _req("INDEPENDENT")

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.worker.profiles._load_resource_spec",
                    new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
                )
            )
            stack.enter_context(
                patch(
                    "app.worker.profiles._select_pod_by_load",
                    new=AsyncMock(return_value=_FakeDeployment()),
                )
            )
            stack.enter_context(
                patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock())
            )
            sched = stack.enter_context(
                patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock())
            )
            await _do_create_profile(req, mock_db)

        sched.assert_called_once()
        # 位置参数：agent_id, profile_name, user_id, scope_type, scope_target_id, pod_name
        assert sched.call_args.args[0] == req.agent_id
        assert sched.call_args.args[1] == req.profile_name
        assert sched.call_args.args[2] == req.user_id  # INDEPENDENT 带 user_id
        assert sched.call_args.args[3] == "ALL"  # scope_type

    async def test_no_user_id_schedules_seeds_with_none(self, mock_k8s):
        """无 user_id 的 profile：hook 仍触发，user_id=None 透传给 _run_profile_seeds
        （用户信息改为 pull skill 实时查询，user_id 不再用于 seed USER.md）。"""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        req = _req("INDEPENDENT")
        req.user_id = None

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.worker.profiles._load_resource_spec",
                    new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
                )
            )
            stack.enter_context(
                patch(
                    "app.worker.profiles._select_pod_by_load",
                    new=AsyncMock(return_value=_FakeDeployment()),
                )
            )
            stack.enter_context(
                patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock())
            )
            sched = stack.enter_context(
                patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock())
            )
            await _do_create_profile(req, mock_db)

        sched.assert_called_once()
        assert sched.call_args.args[2] is None  # user_id=None
