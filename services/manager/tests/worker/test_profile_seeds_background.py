"""seeds 后台化专项测试。

_do_create_profile 把 SOUL.md/skills 种子化移到 _schedule_profile_seeds 后台任务
（避免 ~10 次串行 exec 超 gateway 10s 超时留 root 半成品 profile）。覆盖：
- 同步路径顺序：profile_create → heal → launch → schedule_seeds（seeds 不在同步路径）
- ensure 不 await seeds（_run_profile_seeds 不被 _do_create_profile await）
- _run_profile_seeds：persona，最后 aclose 释放 session
- user_id 不再触发 USER.md 同步（用户信息改为 pull skill 实时查询）
- seed 内部异常不拖垮任务（外层 try/except + logger.warning）
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_deployment():
    return SimpleNamespace(
        id="dep-1",
        scope_type="ALL",
        scope_target_id=None,
        internal_port_map={"profiles": {}},
        pod_name="pod-x",
    )


def _make_req(profile_name="pn-new", user_id="u" * 32):
    from app.worker.router import CreateProfileRequest

    return CreateProfileRequest(
        agent_id="a" * 32,
        engine_instance_id="p" * 32,
        user_id=user_id,
        group_id="g" * 32,
        profile_type="INDEPENDENT",
        profile_name=profile_name,
    )


def _enter_common_patches(stack: ExitStack, deployment) -> None:
    """_do_create_profile 同步路径依赖的公共 patch（不含 heal/exec/sched，测试单独定）。"""
    stack.enter_context(
        patch(
            "app.worker.profiles._load_resource_spec",
            new=AsyncMock(return_value={"max_profiles_per_pod": 20}),
        )
    )
    stack.enter_context(
        patch("app.worker.profiles._select_pod_by_load", new=AsyncMock(return_value=deployment))
    )
    stack.enter_context(
        patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644))
    )
    stack.enter_context(
        patch("app.worker.profiles._port_map_all", new=AsyncMock(return_value={"pn-new": 8644}))
    )
    stack.enter_context(
        patch(
            "app.worker.profiles.get_engine_runtime", new=MagicMock(return_value={"image": "img"})
        )
    )


@pytest.mark.asyncio
async def test_heal_runs_before_launch_before_seeds_scheduled():
    """同步路径顺序：profile_create → heal → launch → schedule_seeds。

    seeds 不在同步路径里（_seed_persona 不被 _do_create_profile 同步调用）。
    """
    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()  # db.add 同步调用

    call_log: list[str] = []

    async def _heal(*a, **kw):
        call_log.append("heal")

    async def _exec(*a, **kw):
        cmd = (kw.get("commands") or [""])[0]
        if "profile create" in cmd:
            call_log.append("profile_create")
        elif "profile_isolation.py launch" in cmd:
            call_log.append("launch")
        return ""

    def _sched(*a, **kw):
        call_log.append("schedule_seeds")

    with ExitStack() as stack:
        _enter_common_patches(stack, deployment)
        stack.enter_context(patch("app.worker.profiles._heal_profile_runtime_config", new=_heal))
        stack.enter_context(
            patch("app.worker.profiles._seed_persona", new=AsyncMock())
        )  # 不应被同步调用
        # skill-dir 准备走 _load_instance_config；返回 None 跳过（不影响 heal/launch 顺序断言）
        stack.enter_context(
            patch("app.worker.profiles._load_instance_config", new=AsyncMock(return_value=None))
        )
        stack.enter_context(patch("app.worker.profiles._schedule_profile_seeds", new=_sched))
        mock_k8s = stack.enter_context(patch("app.worker.profiles.k8s_manager"))
        mock_k8s.exec_hermes_command = _exec
        mock_k8s.update_nginx_config = AsyncMock()
        await _do_create_profile(req, db)

    assert call_log == ["profile_create", "heal", "launch", "schedule_seeds"]


@pytest.mark.asyncio
async def test_ensure_returns_without_awaiting_seeds():
    """_do_create_profile 调 _schedule_profile_seeds 后即返回，不 await _run_profile_seeds。"""
    from app.worker.router import _do_create_profile

    deployment = _make_deployment()
    req = _make_req()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()  # db.add 同步调用

    with ExitStack() as stack:
        _enter_common_patches(stack, deployment)
        stack.enter_context(
            patch("app.worker.profiles._heal_profile_runtime_config", new=AsyncMock())
        )
        # skill-dir 准备走 _load_instance_config；返回 None 跳过
        stack.enter_context(
            patch("app.worker.profiles._load_instance_config", new=AsyncMock(return_value=None))
        )
        sched = stack.enter_context(
            patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock())
        )
        run_seeds = stack.enter_context(
            patch("app.worker.profiles._run_profile_seeds", new=AsyncMock())
        )
        mock_k8s = stack.enter_context(patch("app.worker.profiles.k8s_manager"))
        mock_k8s.exec_hermes_command = AsyncMock(return_value="")
        mock_k8s.update_nginx_config = AsyncMock()
        result = await _do_create_profile(req, db)

    # ensure 返回了结果（未被 seeds 阻塞）
    assert result["created"] is True
    assert result["port"] == 8644
    # scheduler 被调一次
    sched.assert_called_once()
    # _run_profile_seeds 未被 await（scheduler 已 mock，后台任务不跑）
    run_seeds.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_profile_seeds_calls_persona_then_closes_session():
    """_run_profile_seeds：persona，最后 aclose session。"""
    from app.worker.router import _run_profile_seeds

    db = AsyncMock()
    agen = MagicMock()
    agen.__anext__ = AsyncMock(return_value=db)
    agen.aclose = AsyncMock()

    order: list[str] = []

    async def _persona(*a, **kw):
        order.append("persona")

    with (
        patch("app.worker.profiles.get_manager_db", return_value=agen),
        patch("app.worker.profiles._seed_persona", new=_persona),
    ):
        await _run_profile_seeds("a" * 32, "pn-new", "u" * 32, "ALL", None, "pod-x")

    assert order == ["persona"]
    agen.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_profile_seeds_runs_persona_without_user_id():
    """user_id=None → persona 仍跑（用户信息不再 seed，user_id 仅透传不使用）。"""
    from app.worker.router import _run_profile_seeds

    db = AsyncMock()
    agen = MagicMock()
    agen.__anext__ = AsyncMock(return_value=db)
    agen.aclose = AsyncMock()

    persona = AsyncMock()

    with (
        patch("app.worker.profiles.get_manager_db", return_value=agen),
        patch("app.worker.profiles._seed_persona", new=persona),
    ):
        await _run_profile_seeds("a" * 32, "pn-new", None, "ALL", None, "pod-x")

    persona.assert_awaited_once()
    agen.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_profile_seeds_inner_exception_does_not_crash():
    """_seed_persona 抛异常 → 外层 except 捕获 + logger.warning，session 仍释放，函数不抛。"""
    from app.worker.router import _run_profile_seeds

    db = AsyncMock()
    agen = MagicMock()
    agen.__anext__ = AsyncMock(return_value=db)
    agen.aclose = AsyncMock()

    with (
        patch("app.worker.profiles.get_manager_db", return_value=agen),
        patch("app.worker.profiles._seed_persona", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.worker.profiles.logger") as mock_logger,
    ):
        # 不应抛
        await _run_profile_seeds("a" * 32, "pn-new", "u" * 32, "ALL", None, "pod-x")

    mock_logger.warning.assert_called()
    # session 仍被释放
    agen.aclose.assert_awaited_once()
